import os
import re
from datetime import datetime

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_core.tools import tool

from greennode_agentbase import (
    GreenNodeAgentBaseApp,
    RequestContext,
    PingStatus,
)
from starlette.responses import HTMLResponse, JSONResponse
import json

from greennode_agentbase.memory import MemoryClient
from greennode_agentbase.memory.models import MemoryRecordSearchRequest, MemoryRecordInsertDirectlyRequest
from greennode_agent_bridge import AgentBaseMemoryEvents
from langgraph.config import get_config

load_dotenv()

app = GreenNodeAgentBaseApp()

# --- Memory Configuration ---
# Create a memory with: /agentbase-memory
# Set the memory ID here or via MEMORY_ID env var
MEMORY_ID = os.environ.get("MEMORY_ID", "")
if not MEMORY_ID:
    raise ValueError("MEMORY_ID environment variable is required for memory-enabled agents")

# Strategy ID for long-term memory namespace partitioning
# This is fixed per memory instance — do NOT pass as a tool parameter
MEMORY_STRATEGY_ID = os.environ.get("MEMORY_STRATEGY_ID", "default")

# CheckpointSaver: persists conversation state as events in AgentBase Memory
# This enables multi-turn conversations that survive restarts
checkpointer = AgentBaseMemoryEvents(memory_id=MEMORY_ID)

# MemoryClient: used by long-term memory tools to store/search semantic facts
memory_client = MemoryClient()

# --- LLM Configuration ---
# Uses any OpenAI-compatible LLM provider (GreenNode AIP, OpenAI, Ollama, etc.)
# Set LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL in your .env file.
# For GreenNode AIP: use /agentbase-llm to manage API keys and browse models.
# For other providers: set the appropriate base URL and API key.
# Production: use /agentbase-identity to store API key, inject via @requires_api_key
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
if not LLM_MODEL or not LLM_BASE_URL or not LLM_API_KEY:
    raise ValueError(
        "LLM_MODEL, LLM_BASE_URL, and LLM_API_KEY environment variables are required. "
        "Set them in your .env file or use /agentbase-llm to get a platform API key."
    )

llm = ChatOpenAI(
    model=LLM_MODEL,
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
)


# --- Long-Term Memory Tools (via MemoryClient SDK) ---
# actor_id: retrieved from LangGraph configurable (set in handler via context.user_id)
# strategy_id: app-level config (MEMORY_STRATEGY_ID), fixed per memory instance
# Neither should be exposed as tool parameters to avoid LLM hallucination


def _get_actor_id() -> str:
    """Get actor_id from LangGraph configurable (set during graph.invoke)."""
    config = get_config()
    return config["configurable"].get("actor_id", "default")


def _build_namespace(actor_id: str) -> str:
    """Build memory namespace from strategy_id (app config) and actor_id (runtime config)."""
    return f"/strategies/{MEMORY_STRATEGY_ID}/actors/{actor_id}"


@tool
def remember(fact: str) -> str:
    """Store a fact in long-term memory for later retrieval.

    Args:
        fact: The fact or information to remember.
    """
    namespace = _build_namespace(_get_actor_id())
    memory_client.insert_memory_records_directly(
        id=MEMORY_ID,
        namespace=namespace,
        request=MemoryRecordInsertDirectlyRequest(memory_records=[fact]),
    )
    return f"Remembered: {fact}"


@tool
def recall(query: str) -> str:
    """Search long-term memory for facts relevant to a query.

    Args:
        query: Natural language search query.
    """
    namespace = _build_namespace(_get_actor_id())
    results = memory_client.search_memory_records(
        id=MEMORY_ID,
        namespace=namespace,
        request=MemoryRecordSearchRequest(query=query, limit=10),
    )
    if not results:
        return "No relevant memories found."
    
    memories = []
    for r in results:
        if isinstance(r, dict):
            memory = r.get("memory", "")
            score = r.get("score", 0.0)
        else:
            memory = getattr(r, "memory", "")
            score = getattr(r, "score", 0.0)
        memories.append(f"- {memory} (score: {score:.2f})")
    
    return "\n".join(memories)


# --- Create Agent with Checkpointer ---
# create_agent builds a compiled LangGraph StateGraph with tool-calling support.
# checkpointer: persists conversation state via AgentBase Memory (short-term)
# Long-term memory is handled by remember/recall tools via MemoryClient SDK
agent = create_agent(
    llm,
    tools=[remember, recall],
    system_prompt=(
        "Bạn là Trợ lý Onboarding hỗ trợ các thành viên mới gia nhập đội ngũ QE Team PCT tại Zalopay. "
        "Hãy luôn đọc và tuân thủ các quy tắc bắt buộc sau đây trong suốt quá trình hoạt động:\n"
        "1. KHÔNG ĐOÁN MÒ: Nếu không có thông tin chắc chắn hoặc không biết câu trả lời, hãy thừa nhận rõ ràng rằng bạn không biết, tuyệt đối không được suy đoán hay tự tạo ra thông tin.\n"
        "2. CHỈ TRẢ LỜI CÔNG VIỆC TẠI ZALOPAY: Bạn chỉ được phép trả lời các câu hỏi liên quan trực tiếp đến công việc, quy trình, dự án, công cụ hoặc việc onboarding tại Zalopay. Từ chối trả lời một cách lịch sự đối với bất kỳ câu hỏi nào không liên quan đến Zalopay.\n"
        "3. DÙNG TIẾNG VIỆT: Luôn luôn giao tiếp bằng tiếng Việt.\n"
        "4. QUY TẮC THƯƠNG HIỆU: Chỉ sử dụng duy nhất cách viết 'Zalopay' (chữ Z viết hoa, các chữ còn lại viết thường). Tuyệt đối KHÔNG được viết 'Zalo', 'ZaloPay', hay 'zalopay' trong câu trả lời.\n"
        "5. DÒNG CHÀO BẮT BUỘC: Khi chào mừng người dùng lần đầu tiên hoặc khi người dùng chào bạn, bạn phải dùng chính xác dòng chào sau: 'Xin chào! Tôi là Trợ lý Onboarding của đội ngũ QE Team PCT tại Zalopay.'\n"
        "6. QUY TẮC KẾT THÚC CÂU TRẢ LỜI: Không bao giờ được dùng câu kết thúc dạng 'Bạn có câu hỏi nào khác về công việc tại ZaloPay không?' hay bất kỳ câu hỏi tương tự có chứa Zalo/ZaloPay. Thay vào đó, hãy luôn kết thúc câu trả lời bằng câu: 'Bạn có thắc mắc hay muốn đi vào phần nào ở Zalopay không?'\n"
        "7. Đọc và áp dụng nghiêm ngặt các quy tắc này mỗi khi mở một phiên làm việc (session) mới.\n\n"
        "Khi thành viên mới gia nhập hoặc hỏi thông tin về team/dự án, bạn phải cung cấp đầy đủ thông tin theo cấu trúc các phần sau:\n\n"
        "### 👥 CẤU TRÚC TEAM\n"
        "- **Mô hình Matrix:** Squad trục ngang tập trung phát triển và bàn giao tính năng (Delivery), Function trục dọc tập trung chuyên môn và chất lượng (QE Department).\n"
        "- Squad bao gồm Dev và QE, **KHÔNG CÓ PM (Product Manager)**. Do đó, logic nghiệp vụ nằm trong đầu của Dev và QE; QE giữ vai trò quan trọng trong việc bảo toàn 'trí nhớ sản phẩm' (Product Memory).\n"
        "- QE Department có QE Lead quản lý các QE nằm ở các squad khác nhau.\n"
        "- QE Lead đóng vai trò là 'lá chắn' bảo vệ team trước áp lực từ Squad Lead và có quyền đàm phán lại scope test hoặc lùi deadline khi xảy ra sự cố từ phía bên thứ ba (Dependency).\n\n"
        "### 🏦 PRODUCT DOMAIN\n"
        "- **QE Team PCT** chịu trách nhiệm chính cho các dòng sản phẩm: **P2P** (Ví qua Ví), **IBFT** (Chuyển khoản liên ngân hàng qua số thẻ/số tài khoản), **Send Bill** (Chuyển tiền trong chat, Send/Split Bill), và **Lì Xì** (gửi nhóm, lì xì ngẫu nhiên).\n"
        "- **Dependencies chính:**\n"
        "  - **Team Cashier (PCDCASH):** Quản lý luồng thanh toán và Lịch sử giao dịch (LSGD).\n"
        "  - **Team Promotion:** Cung cấp danh sách voucher/coupon (cơ chế mới: collect -> use).\n"
        "  - **Team MMF:** Đối tác cung cấp nguồn tiền Infina (Tài khoản tích lũy).\n"
        "  - **Lending/BNPL:** Nguồn tiền Tài Khoản Trả Sau.\n"
        "- **Platform hợp lệ:** `ZPA iOS`, `ZPA Android`, `ZPI`, `ZMP`. (Trong đó `ZPA` là Zalopay App, `ZMP` là Mini Program. Không nhầm lẫn miniapp trong ZPA và ZMP).\n\n"
        "### 🔄 QUY TRÌNH LÀM VIỆC (QE PROCESS)\n"
        "- **Delivery-driven:** Áp lực bàn giao tính năng rất lớn.\n"
        "- **Checklist-first:** Ưu tiên viết Checklist trước để kịp tiến độ squad, sau đó cập nhật Testcase chi tiết sau (xử lý nợ kỹ thuật/Tech debt). AI hỗ trợ chuyển đổi nhanh giữa Checklist và Testcase.\n"
        "- **Quy tắc phối hợp:** Mọi issue hiển thị LSGD hoặc đối soát nguồn tiền MMF cần phối hợp review giải pháp giữa 3 team (Transfer, Cashier, MMF). Lỗi tích hợp đối tác phối hợp với Partner Integration Team.\n\n"
        "### 🐛 BUG REPORT FORMAT\n"
        "- Bắt buộc kiểm tra (validate) đầy đủ các trường sau trước khi tạo report: `summary`, `platform`, `environment`, `steps`, `actual result`, `expected result`. Nếu thiếu, phải hỏi lại người dùng để làm rõ.\n"
        "- Platform và Environment phải hợp lệ (`Sandbox`, `Staging` / `STG`, `Real`, `Production`, `RC`, `DRSite`).\n"
        "- **Tuyệt đối không** ghi Priority/Severity vào trường Description.\n"
        "- **Template Markdown bắt buộc:**\n"
        "  ```markdown\n"
        "  ## Bug Report\n"
        "  **Summary:** [Mô tả ngắn gọn]\n"
        "  ### Environment\n"
        "  - **Platform:** [Platform hợp lệ]\n"
        "  - **Environment:** [Env hợp lệ]\n"
        "  ### Steps to Reproduce\n"
        "  1. [Bước 1]\n"
        "  ### Actual Result\n"
        "  [Kết quả thực tế]\n"
        "  ### Expected Result\n"
        "  [Kết quả mong đợi]\n"
        "  ```\n\n"
        "### 🧪 TESTCASES RULE\n"
        "- **Clarification Rules:** Khi user yêu cầu viết testcase chung chung, tuyệt đối **KHÔNG** viết ngay mà phải đặt 5 câu hỏi làm rõ: 1. Test Layer (API, UI, hay E2E)? 2. Mục đích test? 3. Tài liệu (PRD, Tech design) có chưa? 4. Requirement đã chốt chưa? 5. Risks & Impacts là gì?\n"
        "- **Format Testcase:** Mỗi test case phải có đủ: `TC-[ID]`, `Platform` (hợp lệ), `Priority` (P1-P4), `Related`, `Pre-condition`, `Steps` (danh sách đánh số), `Expected Result`.\n"
        "- **API Focus & Risk-based (Tập trung vào API thay vì Frontend):**\n"
        "  - **Test Idempotency (giao dịch trùng lặp):** Tập trung kiểm thử các outcome của Redis state machine (`ACQUIRED`, `REPLAY`, `IN_PROGRESS`, `PAYLOAD_MISMATCH`, `INVALID_REQUEST`) thông qua gRPC service `MTIbftAPI` (IdempotencyAcquire & IdempotencyComplete).\n"
        "  - **MMF Technical Mocking:** Sử dụng mock amount đặc biệt (`31000`, `131001` -> `131005`, `161000`) để trigger lỗi tích hợp từ phía backend MMF.\n\n"
        "Hãy sử dụng công cụ 'remember' để ghi nhớ các dữ kiện quan trọng về người dùng và 'recall' để tìm kiếm lại khi cần."
    ),
    checkpointer=checkpointer,
)



@app.entrypoint
def handler(payload: dict, context: RequestContext) -> dict:
    """Main agent entrypoint with LangChain + Memory support.

    Args:
        payload: JSON body with "message"
        context: Request metadata (session_id, user_id, request_headers)
    """
    # Short-term memory (checkpointer) requires both user_id and session_id
    # to correctly persist and isolate conversation state per user per session.
    if not context.user_id or not context.session_id:
        return {
            "status": "error",
            "error": "Missing required headers: X-GreenNode-AgentBase-User-Id and X-GreenNode-AgentBase-Session-Id are required when using memory.",
        }

    message = payload.get("message", "Hello")

    # Map AgentBase context to LangGraph config
    # thread_id -> session persistence, actor_id -> per-user memory
    config = {
        "configurable": {
            "thread_id": context.session_id,
            "actor_id": context.user_id,
        }
    }

    result = agent.invoke(
        {"messages": [{"role": "user", "content": message}]},
        config=config,
    )
    ai_message = result["messages"][-1]
    
    # Post-processing to enforce brand spelling "Zalopay" strictly
    response_text = ai_message.content
    response_text = re.sub(r'(?i)zalo\s*pay', 'Zalopay', response_text)
    response_text = re.sub(r'(?i)\bzalo\b', 'Zalopay', response_text)
    
    return {
        "status": "success",
        "response": response_text,
        "timestamp": datetime.now().isoformat(),
    }


@app.ping
def health_check() -> PingStatus:
    """Custom health check for GET /health endpoint."""
    return PingStatus.HEALTHY


async def serve_index(request):
    try:
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(html_content)
    except Exception as e:
        return HTMLResponse(f"<html><body><h1>Error loading UI: {str(e)}</h1></body></html>", status_code=500)

FEEDBACK_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback_logs.jsonl")

async def submit_feedback(request):
    try:
        data = await request.json()
        user_message = data.get("user_message", "")
        bot_response = data.get("bot_response", "")
        rating = data.get("rating", "")
        user_id = request.headers.get("X-GreenNode-AgentBase-User-Id", "anonymous")
        session_id = request.headers.get("X-GreenNode-AgentBase-Session-Id", "anonymous")
        
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "session_id": session_id,
            "user_message": user_message,
            "bot_response": bot_response,
            "rating": rating
        }
        
        with open(FEEDBACK_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            
        return JSONResponse({"status": "success"})
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)

app.add_route("/feedback", submit_feedback, methods=["POST"])
app.add_route("/", serve_index, methods=["GET"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(port=port, host="0.0.0.0")
