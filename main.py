import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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

FEEDBACK_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback_logs.jsonl")
CHAT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_logs.jsonl")


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
    temperature=0.0,
)


# --- Long-Term Memory Tools (via MemoryClient SDK) ---
# actor_id: retrieved from LangGraph configurable (set in handler via context.user_id)
# strategy_id: app-level config (MEMORY_STRATEGY_ID), fixed per memory instance
# Neither should be exposed as tool parameters to avoid LLM hallucination


def _get_actor_id() -> str:
    """Get actor_id from LangGraph configurable (set during graph.invoke)."""
    config = get_config()
    return config["configurable"].get("actor_id", "default")


# Shared namespace for team-wide knowledge (IBFT guides, testing docs, etc.)
# Insert here to make knowledge available to ALL users without redeployment.
SHARED_ACTOR_ID = "default"


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
    """Search long-term memory for detailed information about the QE team at Zalopay.

    ALWAYS call this tool when the user asks about:
    - P2P / Ví qua Ví product details, flows, or test cases
    - IBFT / chuyển khoản liên ngân hàng details
    - Testing strategy, test levels, test types, or test pyramid at Zalopay
    - Specific tools: Jira, Confluence, TestLink, Postman, Jenkins, K6, JMeter, etc.
    - Bug taxonomy, bug report formats, test environments (Sandbox/Staging/Production)
    - Onboarding documentation content from Confluence pages
    - Any detailed technical information that may have been stored from documentation

    Also call this for:
    - Custom instructions or rules the user told you to remember
    - Personal facts or preferences about the current user

    Args:
        query: Natural language search query describing what you need to find.
    """
    # Prioritize local exact match from confidential_knowledge.json to ensure 100% accuracy
    try:
        confidential_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "confidential_knowledge.json")
        if os.path.exists(confidential_path):
            with open(confidential_path, "r", encoding="utf-8") as f:
                conf_data = json.load(f)
            
            q_lower = query.lower()
            local_results = []
            
            # Check if query matches Kafka Idempotency keywords
            if any(k in q_lower for k in ["kafka", "idempotency", "idem", "callback", "event", "consumer", "handler", "pct-18986", "pct-21220", "pct-21221", "pct-21222"]):
                ctx = conf_data.get("project_context", {})
                local_results.append(f"**Thông tin Dự án Idempotency Kafka (Jira {ctx.get('jira_ticket')}):**")
                local_results.append(f"- Tiêu đề: {ctx.get('title')}")
                local_results.append(f"- Mô tả: {ctx.get('description')}")
                local_results.append("- Các hành động tiếp theo:")
                for action in ctx.get("next_actions", []):
                    local_results.append(f"  * {action}")
                local_results.append("- Lưu ý nghiệp vụ:")
                for note in ctx.get("notes", []):
                    local_results.append(f"  * {note}")
                local_results.append("")
                
                # Check for specific consumer match or append all
                consumers = conf_data.get("consumers", [])
                matched_consumers = []
                for c in consumers:
                    combined_lower = (c["service"] + " " + c["topic"] + " " + c["handler"] + " " + c["ticket"]).lower()
                    parts = re.split(r'[_.\\-*/\\s]+', combined_lower)
                    parts = [p for p in parts if len(p) > 2]
                    
                    if any(k in q_lower for k in parts) or any(t.lower() in q_lower for t in [c["service"], c["topic"], c["handler"], c["ticket"]]):
                        matched_consumers.append(c)
                
                consumers_to_show = matched_consumers if matched_consumers else consumers
                local_results.append("**Chi tiết các Consumer Topic & Idempotency Key:**")
                for c in consumers_to_show:
                    local_results.append(f"- Service: `{c['service']}`")
                    local_results.append(f"  * Topic: `{c['topic']}`")
                    local_results.append(f"  * Handler: `{c['handler']}`")
                    local_results.append(f"  * Mô tả: {c['description']}")
                    local_results.append(f"  * Ảnh hưởng trùng lặp: {c['impact']}")
                    local_results.append(f"  * **Idempotency Key:** `{c['idempotency_key']}`")
                    local_results.append(f"  * TTL: {c['ttl']}")
                    local_results.append(f"  * Ticket: `{c['ticket']}`")
                local_results.append("")

            # Check if query matches Client Layer keywords
            if any(k in q_lower for k in ["client layer", "framework", "package manager", "build tool", "mcro-app-cli", "react 17"]):
                cl = conf_data.get("transfer_client_layer", {})
                if cl:
                    local_results.append("**Thông tin Client Layer của mảng Transfer:**")
                    local_results.append(f"- App: `{cl.get('app')}`")
                    local_results.append(f"- Framework: `{cl.get('framework')}`")
                    local_results.append(f"- Build Tool: `{cl.get('build_tool')}`")
                    local_results.append(f"- Package Manager: `{cl.get('package_manager')}`")
                    local_results.append("")

            # Check if query matches P2P Performance keywords
            if any(k in q_lower for k in ["p2p", "performance", "objective", "specification", "test scenario", "virtual users"]):
                rep = conf_data.get("p2p_performance_test_report", {})
                if rep:
                    local_results.append("**Thông tin Báo cáo Hiệu năng P2P (P2P Performance Test Report):**")
                    local_results.append("- **Objective (Mục tiêu):**")
                    for obj in rep.get("objective", []):
                        local_results.append(f"  * {obj}")
                    local_results.append("- **Testing Specifications (Thông số kỹ thuật/Service tham gia test):**")
                    for spec in rep.get("testing_specifications", []):
                        local_results.append(f"  * {spec}")
                    local_results.append("")

            if local_results:
                return "\n".join(local_results).strip()
    except Exception as e:
        print("Error checking local confidential knowledge:", e)

    actor_id = _get_actor_id()
    user_namespace = _build_namespace(actor_id)
    shared_namespace = _build_namespace(SHARED_ACTOR_ID)

    def _search(namespace: str) -> list:
        try:
            results = memory_client.search_memory_records(
                id=MEMORY_ID,
                namespace=namespace,
                request=MemoryRecordSearchRequest(query=query, limit=5),
            )
            return results or []
        except Exception:
            return []

    def _extract(r) -> tuple[str, float]:
        if isinstance(r, dict):
            return r.get("memory", ""), r.get("score", 0.0)
        return getattr(r, "memory", ""), getattr(r, "score", 0.0)

    # Run both namespace searches concurrently to minimize latency
    namespaces = {"shared": shared_namespace}
    if actor_id != SHARED_ACTOR_ID:
        namespaces["user"] = user_namespace

    all_results: list = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(_search, ns): ns for ns in namespaces.values()}
        for future in as_completed(futures):
            all_results.extend(future.result())

    # Merge and deduplicate by memory content, keeping highest score per entry
    seen: dict[str, float] = {}
    for r in all_results:
        memory, score = _extract(r)
        if memory and (memory not in seen or score > seen[memory]):
            seen[memory] = score

    if not seen:
        return "No relevant memories found."

    # Sort by score descending, cap at 10 results total
    ranked = sorted(seen.items(), key=lambda x: x[1], reverse=True)[:10]
    return "\n".join(f"- {mem}" for mem, score in ranked)


# --- Create Agent with Checkpointer ---
# Load system prompt from file (git-ignored for security/confidentiality)
try:
    prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
except Exception as e:
    SYSTEM_PROMPT = "Bạn là Trợ lý Onboarding hỗ trợ riêng cho các thành viên QE mới gia nhập (không dành cho Frontend, Developer, hay PO) thuộc đội ngũ QE_Consumer Team tại Zalopay. Bạn có thắc mắc hay muốn đi vào phần nào ở Zalopay không?"

# create_agent builds a compiled LangGraph StateGraph with tool-calling support.
# checkpointer: persists conversation state via AgentBase Memory (short-term)
# Long-term memory is handled by remember/recall tools via MemoryClient SDK
agent = create_agent(
    llm,
    tools=[remember, recall],
    system_prompt=SYSTEM_PROMPT,
    checkpointer=checkpointer,
)



@app.entrypoint
def handler(payload: dict, context: RequestContext):
    """Main agent entrypoint with LangChain + Memory support.

    Args:
        payload: JSON body with "message"
        context: Request metadata (session_id, user_id, request_headers)
    """
    async def stream_generator():
        # Short-term memory (checkpointer) requires both user_id and session_id
        # to correctly persist and isolate conversation state per user per session.
        if not context.user_id or not context.session_id:
            yield {
                "status": "error",
                "error": "Missing required headers: X-GreenNode-AgentBase-User-Id and X-GreenNode-AgentBase-Session-Id are required when using memory.",
            }
            return

        message = payload.get("message", "Hello")

        # Map AgentBase context to LangGraph config
        # thread_id -> session persistence, actor_id -> per-user memory
        config = {
            "configurable": {
                "thread_id": context.session_id,
                "actor_id": context.user_id,
            }
        }

        accumulated_text = ""
        sent_length = 0

        try:
            async for chunk, metadata in agent.astream(
                {"messages": [{"role": "user", "content": message}]},
                config=config,
                stream_mode="messages"
            ):
                content = getattr(chunk, 'content', '')
                if content:
                    accumulated_text += content
                    
                    # Post-processing to enforce brand spelling "Zalopay" strictly
                    corrected_text = re.sub(r'(?i)zalo\s*pay', 'Zalopay', accumulated_text)
                    corrected_text = re.sub(r'(?i)\bzalo\b', 'Zalopay', corrected_text)
                    
                    if len(corrected_text) > sent_length:
                        delta = corrected_text[sent_length:]
                        sent_length = len(corrected_text)
                        yield {
                            "status": "success",
                            "response": delta,
                            "timestamp": datetime.now().isoformat(),
                        }
        except Exception as e:
            yield {
                "status": "error",
                "error": f"Lỗi trong quá trình streaming: {str(e)}",
            }
            return

        # Log the full question and response
        try:
            final_response = re.sub(r'(?i)zalo\s*pay', 'Zalopay', accumulated_text)
            final_response = re.sub(r'(?i)\bzalo\b', 'Zalopay', final_response)
            
            chat_entry = {
                "timestamp": datetime.now().isoformat(),
                "user_id": context.user_id,
                "session_id": context.session_id,
                "user_message": message,
                "bot_response": final_response
            }
            with open(CHAT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(chat_entry, ensure_ascii=False) + "\n")
        except Exception as log_err:
            print("Logging error:", log_err)

    return stream_generator()



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
async def serve_training_data(request):
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training_data.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
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
app.add_route("/training-data", serve_training_data, methods=["GET"])
app.add_route("/", serve_index, methods=["GET"])



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(port=port, host="0.0.0.0")
