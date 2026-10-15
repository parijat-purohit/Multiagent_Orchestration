from datetime import datetime
import chromadb
from google.adk.agents.llm_agent import Agent
import pandas as pd
import re
from pypdf import PdfReader
import requests

EXCEL_PATH = (
    "data/steps_analysis.xlsx"
)
PDF_PATH = "data/walking_coach_policies.pdf"


# =====================================================================
# 1. CHROMADB VECTOR DATABASE SETUP (Loads the PDF)
# =====================================================================
chroma_client = chromadb.Client()
policy_collection = chroma_client.get_or_create_collection(
    name="walking_coach_policies"
)


def load_pdf_into_vector_db():
    """Properly chunks walking_coach_policies.pdf into EXACTLY 4 policies."""
    # Reset the collection so we don't keep the old 7 dirty chunks
    global policy_collection
    try:
        chroma_client.delete_collection(name="walking_coach_policies")
    except Exception:
        pass
    policy_collection = chroma_client.create_collection(
        name="walking_coach_policies"
    )
    # 1. Read PDF
    reader = PdfReader(PDF_PATH)
    full_text = "\n".join([page.extract_text() for page in reader.pages])
    # 2. Strip page numbers and headers
    clean_text = re.sub(r"Page \d+ \|.*?\n", "", full_text)
    clean_text = re.sub(
        r"Personalized Walking & Health Policy Guidelines\nStandard Operating"
        r" Procedures.*?\n",
        "",
        clean_text,
    )
    # 3. Match specifically on 'Policy 1:', 'Policy 2:', etc.
    parts = re.split(r"(Policy \d+:)", clean_text)
    policy_chunks = []
    for i in range(1, len(parts), 2):
        title = parts[i]
        body = parts[i + 1].strip()
        policy_chunks.append(f"{title} {body}")
    # 4. Add exactly 4 clean policies to ChromaDB
    policy_collection.add(
        documents=policy_chunks,
        ids=[f"policy_{i+1}" for i in range(len(policy_chunks))],
    )
    print(
        f"✅ Loaded exactly {len(policy_chunks)} clean policies into ChromaDB.")


# Initialize vector DB on startup
load_pdf_into_vector_db()


# =====================================================================
# 2. DEFINE TOOLS
# =====================================================================


def search_policy_vector_db(query: str) -> dict:
    """RAG Tool: Performs semantic vector search on walking_coach_policies.pdf using ChromaDB.

    Args:
        query: What concept to look up (e.g. 'hot weather hydration', 'joint
          fatigue 8000 steps', 'cold rainy limit').
    """
    results = policy_collection.query(
        query_texts=[query],
        n_results=1,
        include=["documents", "distances", "embeddings"],
    )
    matched = (
        results["documents"][0][0]
        if results["documents"]
        else "No policy matched."
    )
    distance = results["distances"][0][0] if results["distances"] else None

    return {
        "query": query,
        "search_engine": "ChromaDB (Vector RAG)",
        "similarity_distance": distance,
        "retrieved_policy": matched,
    }


def get_all_vector_data() -> dict:
    """RAG Inspection Tool: Retrieves all stored policies, their text chunks, and embedding vectors from ChromaDB."""
    data = policy_collection.get(include=["documents", "embeddings"])

    entries = []
    for i in range(len(data["ids"])):
        emb = data["embeddings"][i] if data["embeddings"] is not None else []
        sample_values = (
            [round(float(v), 5) for v in emb[:6]] if len(emb) else []
        )

        entries.append(
            {
                "id": data["ids"][i],
                "policy_preview": data["documents"][i][:140].strip() + "...",
                "vector_dimensions": len(emb),
                "embedding_sample_first_6": sample_values,
            }
        )

    return {
        "collection_name": "walking_coach_policies",
        "total_documents": len(entries),
        "policies": entries,
    }


def get_current_date() -> dict:
    """Gets today's date, day of week, and full date from the system clock."""
    now = datetime.now()
    return {
        "today_date": now.strftime("%Y-%m-%d"),
        "day_of_week": now.strftime("%A"),
        "formatted": now.strftime("%B %d, %Y"),
    }


def get_weather(city: str) -> dict:
    """Gets current outdoor weather for a specified city online."""
    try:
        response = requests.get(
            f"https://wttr.in/{city}?format=j1",
            headers={"User-Agent": "WeatherAssistant/1.0"},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
        current = data["current_condition"][0]

        desc = current["weatherDesc"][0]["value"]
        temp_c = int(current["temp_C"])
        temp_f = int(current["temp_F"])

        return {
            "city": city,
            "condition": desc,
            "temperature_c": f"{temp_c}°C",
            "temperature_f": f"{temp_f}°F",
            "is_sunny": any(
                w in desc.lower() for w in ["sunny", "clear", "partly cloudy"]
            ),
        }
    except Exception as e:
        return {"error": f"Failed to retrieve weather for '{city}': {str(e)}"}


def get_historical_steps(target_date: str = None) -> dict:
    """Finds steps walked on this calendar day in prior years (2025, 2024, etc.).

    Defaults to today's system date if not provided.
    """
    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    dt = pd.to_datetime(target_date)
    df = pd.read_excel(EXCEL_PATH, sheet_name="Daily_Summary")

    matches = df[
        (df["month"] == dt.month)
        & (df["day"] == dt.day)
        & (df["year"] < dt.year)
    ].sort_values("year", ascending=False)
    if matches.empty:
        return {
            "message": f"No prior year data found for {dt.strftime('%B %d')}."
        }

    breakdown = {int(r["year"]): int(r["steps"])
                 for _, r in matches.iterrows()}
    return {
        "calendar_day": dt.strftime("%B %d"),
        "years": breakdown,
        "historical_average": round(float(matches["steps"].mean()), 1),
    }


def get_recent_7_days(target_date: str = None) -> dict:
    """Fetches walking records for the last 7 days prior to target_date.

    Defaults to today's system date if not provided.
    """
    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    df = pd.read_excel(EXCEL_PATH, sheet_name="Daily_Summary")
    recent = (
        df[df["date"] < target_date]
        .sort_values("date", ascending=False)
        .head(7)
    )

    records = [
        {"date": r["date"], "day": r["dayOfWeek"], "steps": int(r["steps"])}
        for _, r in recent.iterrows()
    ]
    avg_steps = round(float(recent["steps"].mean()), 1)

    return {
        "recent_7_days": records,
        "7_day_average": avg_steps,
        "is_high_volume": avg_steps >= 8000,
    }


# =====================================================================
# 3. DEFINE THE AGENTS
# =====================================================================

# Agent 1: Historical Analytics Agent
historical_analytics_agent = Agent(
    name="historical_analytics_agent",
    model="gemini-3.8-flash",
    description="Specialist that looks up walking step history for this calendar day in prior years and computes averages.",
    instruction=(
        "You are an analytics agent. When asked about past years on a date: "
        "1. Call 'get_historical_steps' (defaults to today's date). "
        "2. List each prior year specifically (e.g., In 2025: X steps, In 2024: Y steps). "
        "3. State the average steps clearly."
    ),
    tools=[get_historical_steps, get_current_date],
)

# Agent 2: RAG Inspector Agent (New!)
rag_inspector_agent = Agent(
    name="rag_inspector_agent",
    model="gemini-3.8-flash",
    description="Specialist debugging agent that inspects and displays raw ChromaDB vector data, policy chunks, and numerical embedding coordinates.",
    instruction=(
        "You are the Vector Database Inspector. "
        "When the user asks to see vector data, inspect ChromaDB, or view embeddings (e.g. 'show me the vector data'): "
        "1. Call 'get_all_vector_data'. "
        "2. Present each policy clearly with its ID, text preview, vector dimensions (e.g. 384 numbers), and the sample float values."
    ),
    tools=[get_all_vector_data],
)

# Agent 3: Prescriptive Agent (Root)
today_info = datetime.now().strftime("%A, %B %d, %Y")
root_agent = Agent(
    name="prescriptive_walking_coach",
    model="gemini-3.8-flash",
    description="Prescriptive walking coach governed strictly by vector-retrieved rules from walking_coach_policies.pdf.",
    instruction=(
        f"You are a personal walking coach. Today's system date is {today_info}. "
        "Always evaluate today automatically unless the user specifies another date. "
        "\nROUTING INSTRUCTIONS:\n"
        "- If the user specifically asks to see vector data, inspect ChromaDB, or view embeddings "
        "  (e.g., 'show me the vector data'), delegate immediately to 'rag_inspector_agent'.\n"
        "- If the user asks about prior years or historical averages, delegate to 'historical_analytics_agent'.\n"
        "- For walking advice, follow this Vector RAG procedure:\n"
        "  1. Call 'get_recent_7_days' and 'historical_analytics_agent' for step numbers.\n"
        "  2. Call 'get_weather' if city is mentioned or relevant.\n"
        "  3. Call 'search_policy_vector_db' with semantic queries (e.g. 'hot weather hydration', 'joint fatigue 8000 steps') "
        "     to retrieve the official policy rules.\n"
        "  4. Formulate your final advice strictly adhering to the policies returned by 'search_policy_vector_db'."
    ),
    tools=[
        get_recent_7_days,
        get_weather,
        get_current_date,
        search_policy_vector_db,
    ],
    # Both specialized sub-agents are registered here:
    sub_agents=[historical_analytics_agent, rag_inspector_agent],
)
