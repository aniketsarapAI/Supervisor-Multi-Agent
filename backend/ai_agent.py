# IMPORT PACKAGES

from langgraph.graph import StateGraph
from dotenv import load_dotenv

from utils import AgentState, SUPERVISOR, GREETING, ENHANCER, CODER, RESEARCHER, MATHS_REASONER, SHOULD_USE_TOOLS, TOOLS, supervisor_node, greeting_node, enhancer_node, should_use_tools_node, use_tools_node, coder_node, maths_reasoner_node, researcher_node

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

import asyncio
import logging
import os

logging.getLogger("langgraph").setLevel(logging.ERROR)
logging.getLogger("langgraph.pregel").setLevel(logging.ERROR)

load_dotenv()

# GRAPH CONSTANTS

graph = StateGraph(AgentState)

graph.add_node(ENHANCER, enhancer_node)
graph.add_node(GREETING, greeting_node)
graph.add_node(CODER, coder_node)
graph.add_node(MATHS_REASONER, maths_reasoner_node)
graph.add_node(RESEARCHER, researcher_node)
graph.add_node(SHOULD_USE_TOOLS, should_use_tools_node)
graph.add_node(TOOLS, use_tools_node)
graph.add_node(SUPERVISOR, supervisor_node)

graph.set_entry_point(SUPERVISOR)

DB_URI = os.getenv("SUPABASE_DB_URL", "")


async def graph_builder():
    """Compile the graph with a persistent checkpointer (PostgresSaver) if DB_URI is set,
    otherwise fall back to MemorySaver for local dev."""
    if DB_URI:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            conninfo=DB_URI,
            max_size=10,
            min_size=2,
            kwargs={"autocommit": True, "prepare_threshold": None},
            open=False,
        )
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool)
        try:
            await checkpointer.setup()
        except Exception as e:
            logging.getLogger(__name__).warning(f"checkpointer.setup() warning (likely already set up): {e}")
        app = graph.compile(checkpointer=checkpointer)
    else:
        app = graph.compile(checkpointer=MemorySaver())

    return app


if __name__ == "__main__":

    async def main():
        memory_config = {"configurable": {"thread_id": 1}}
        app = await graph_builder()

        while True:
            user_prompt = input("\n\nUser: ")
            if user_prompt.lower() == "q":
                break

            snapshot = app.get_state(config=memory_config)
            if snapshot and "messages" in snapshot.values:
                old_msgs = snapshot.values["messages"]
            else:
                old_msgs = []

            events = app.astream_events(
                input=AgentState(messages=old_msgs + [HumanMessage(content=user_prompt)]),
                version="v2",
                config=memory_config,
            )

            print(f"\nAI: ", end="", flush=True)

            async for event in events:
                if event["event"] == "on_chat_model_stream":
                    print(event["data"]["chunk"].content, end="", flush=True)

    asyncio.run(main())