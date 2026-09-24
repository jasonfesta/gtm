"""Post score for agent tool discovery. Not generic developers or generic agent launches."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

POLICY = "assistant-builder-v1"
COMMENT_MIN = 40
COMMENT_BUILDER = 10
COMMENT_SPEC = 10
LATER_MIN = 40
LATER_SPEC = 20

CONSUMER = (
    r"asked (chatgpt|claude|gemini|copilot)",
    r"(chatgpt|claude|gemini) (said|told|wrote|generated)",
    r"using (cursor|copilot|chatgpt|claude) to (write|code|draft)",
    r"prompt i (used|wrote) for",
)
GENERIC_DEV = (
    r"software engineer",
    r"full[- ]stack",
    r"backend developer",
    r"frontend developer",
    r"learn to code",
    r"hiring (a |an )?(software |backend |frontend )?(engineer|developer)",
)
EXCLUDE = ("airdrop", "memecoin", "token sale", "nft mint", "wallet drain")
BOT = re.compile(r"\[bot\]$|\bdependabot\b|\bgithub-actions\b", re.I)
AGENT_TOPIC = (
    "ai agent",
    "agentic",
    "mcp server",
    "mcp",
    "agent eval",
    "agent memory",
    "agent orchestration",
    "multi agent",
    "multi-agent",
    "agent skill",
    "voice agent",
    "browser agent",
    "coding agent",
    "agent sdk",
    "agent guardrail",
    "agentic rag",
    "agent developer",
    "building an agent",
    "building agents",
    "tool discovery",
    "agent routing",
    "tool selection",
    "tool calling",
    "function calling",
    "agent tools",
    "x402",
    "a2a",
    "webmcp",
    "web mcp",
    "ucp",
    "agent payments",
    "agentic payments",
    "agentic commerce",
    "our agent",
    "my agent",
)
ASSISTANT_TOPIC = (
    "ai assistant",
    "assistant developer",
    "building an assistant",
    "creating an assistant",
    "our assistant",
    "my assistant",
)
ASSISTANT_AGENT = AGENT_TOPIC + ASSISTANT_TOPIC
CONCRETE = (
    "mcp",
    "mcp server",
    "tool discovery",
    "tool catalog",
    "tool routing",
    "tool loop",
    "tool calling",
    "function calling",
    "x402",
    "a2a",
    "webmcp",
    "web mcp",
    "ucp",
    "agent payments",
    "agentic payments",
    "agentic commerce",
    "agent memory",
    "agent eval",
    "guardrail",
    "orchestration",
    "handoff",
    "context window",
    "agent skill",
    "langgraph",
    "crewai",
    "agents sdk",
    "claude agent sdk",
)
BUILDER = (
    r"\b(i|we|our|my)\b.{0,48}\b(built|building|shipped|shipping|implementing|launched|wrote|writing)\b",
    r"\b(our|my) (ai )?agents?\b",
    r"\b(i|we) (run|ran|added|wired) (an? )?(mcp|webmcp|a2a|ucp|agent|assistant)\b",
    r"\b(i|we|our|my)\b.{0,64}\b(tool discovery|tool catalog|mcp server|mcp|webmcp|web mcp|a2a|ucp|tool loop|tool routing|x402|agent payments)\b",
)
FRAMEWORK = (
    "langchain",
    "langgraph",
    "crewai",
    "openai agents",
    "claude agent",
    "google adk",
    "vercel ai sdk",
)
# Protocol names (x402, A2A, WebMCP, MCP, UCP) are agent-topic, not enough alone.
# Comment only when the post is about discovering, finding, loading, or routing tools.
TOOL_DISCOVERY = (
    "tool calling",
    "function calling",
    "tool loop",
    "tool discovery",
    "tool catalog",
    "tool routing",
    "tool-routing",
    "tool selection",
    "agent tools",
    "agent's tools",
    "tools for agents",
    "mcp tools",
    "webmcp tools",
    "discover tools",
    "discovers tools",
    "find tools",
    "finding tools",
    "tool use",
    "first load",
    "hardcoding",
    "hardcoded",
    "up front",
    "upfront",
    "without a dashboard",
    "without the dashboard",
    "payment tools",
    "agentic search",
    "search for tools",
    "search for ais",
    "search for agents",
    "which mcp",
    "what tools",
    "tools did you",
    "how mcp",
    "mcps are added",
    "add new mcp",
    "living toolset",
)


def _blob(item):
    return " ".join(
        str(item.get(key) or "") for key in ("title", "text", "query", "name")
    ).casefold()


def _any_re(blob, patterns):
    return [p for p in patterns if re.search(p, blob)]


def _any_term(blob, terms):
    return [term for term in terms if term in blob]


def tool_discovery_hit(*parts):
    """True when the text is actually about agent tool discovery."""
    blob = " ".join(str(part or "") for part in parts).casefold()
    return bool(_any_term(blob, TOOL_DISCOVERY))


def score_post(item):
    """Deterministic offline scorer used by tests and policy calibration."""
    blob = _blob(item)
    text = str(item.get("text") or "")
    if _any_term(blob, EXCLUDE):
        return _result(0, 0, 0, "skip", "excluded_promotion")
    consumer = _any_re(blob, CONSUMER)
    generic = _any_re(blob, GENERIC_DEV)
    agent = _any_term(blob, ASSISTANT_AGENT)
    concrete = _any_term(blob, CONCRETE)
    builder = _any_re(blob, BUILDER)
    framework = _any_term(blob, FRAMEWORK)
    if not agent and (consumer or (generic and not framework)):
        return _result(0, 0, 0, "skip", "generic_or_consumer")
    if agent and (builder or concrete):
        specificity = 30
    elif agent:
        specificity = 20
    elif framework or concrete:
        specificity = 10
    else:
        specificity = 0
    if builder and agent and concrete:
        builder_score = 50
    elif builder and agent:
        builder_score = 35
    elif agent and concrete:
        builder_score = 20
    elif agent and "?" in text:
        builder_score = 20
    elif agent:
        builder_score = 10
    else:
        builder_score = 0
    if concrete and len(text.strip()) >= 80:
        substance = 20
    elif agent and (builder or "?" in text or concrete):
        substance = 10
    else:
        substance = 0
    total = builder_score + specificity + substance
    channel = item.get("channel")
    url = (item.get("parent_url") or item.get("url") or "").strip()
    name = (item.get("name") or "").strip()
    profile = (item.get("profile_url") or "").strip()
    if not url.startswith("https://"):
        return _result(builder_score, specificity, substance, "skip", "missing_url")
    if not name or not profile.startswith("https://"):
        return _result(builder_score, specificity, substance, "skip", "incomplete_identity")
    if channel == "github" and (BOT.search(name) or BOT.search(profile)):
        return _result(builder_score, specificity, substance, "skip", "bot_identity")
    tools = bool(_any_term(blob, TOOL_DISCOVERY))
    agent_topic = bool(_any_term(blob, AGENT_TOPIC))
    assistant_topic = bool(_any_term(blob, ASSISTANT_TOPIC))
    building = bool(builder)
    if channel in ("x", "linkedin"):
        if not (item.get("text") or "").strip():
            return _result(
                builder_score,
                specificity,
                substance,
                "skip",
                "missing_text",
                tool_discovery=tools,
                topic="",
            )
        if agent_topic:
            if not tools:
                return _result(
                    builder_score,
                    specificity,
                    substance,
                    "skip",
                    "agent_not_tool_discovery",
                    tool_discovery=False,
                    topic="agent",
                )
            return _result(
                builder_score,
                specificity,
                substance,
                "comment",
                "agent_tool_discovery",
                tool_discovery=True,
                topic="agent",
            )
        if assistant_topic and building:
            if not tools:
                return _result(
                    builder_score,
                    specificity,
                    substance,
                    "skip",
                    "assistant_not_tool_discovery",
                    tool_discovery=False,
                    topic="assistant",
                )
            return _result(
                builder_score,
                specificity,
                substance,
                "comment",
                "assistant_tool_discovery",
                tool_discovery=True,
                topic="assistant",
            )
        if assistant_topic:
            return _result(
                builder_score,
                specificity,
                substance,
                "skip",
                "assistant_not_building",
                tool_discovery=tools,
                topic="assistant",
            )
        return _result(
            builder_score,
            specificity,
            substance,
            "skip",
            "below_comment_floor",
            tool_discovery=tools,
        )
    if channel in ("hacker_news", "github", "product_hunt", "apollo"):
        if total >= LATER_MIN and specificity >= LATER_SPEC:
            return _result(
                builder_score, specificity, substance, "later_use", "assistant_agent_source"
            )
        return _result(builder_score, specificity, substance, "skip", "below_later_floor")
    return _result(builder_score, specificity, substance, "skip", "unsupported_channel")


def _result(builder, specificity, substance, action, reason, tool_discovery=False, topic=""):
    return {
        "policy": POLICY,
        "builder": builder,
        "specificity": specificity,
        "substance": substance,
        "total": builder + specificity + substance,
        "action": action,
        "reason": reason,
        "tool_discovery": tool_discovery,
        "topic": topic,
    }


def score_post_via_portkey(item, *, complete_fn=None):
    """Score one live X post through verified Portkey gpt-4o-mini or fail closed."""
    from crm.portkey import complete

    complete_fn = complete_fn or complete
    payload = {
        key: str(item.get(key) or "")
        for key in ("channel", "name", "profile_url", "parent_url", "text", "query")
    }
    if payload["channel"] != "x":
        return _result(0, 0, 0, "skip", "channel_paused")
    if not payload["name"] or not payload["profile_url"].startswith("https://"):
        return _result(0, 0, 0, "skip", "incomplete_identity")
    if not payload["parent_url"].startswith("https://") or not payload["text"].strip():
        return _result(0, 0, 0, "skip", "missing_post_evidence")
    prompt = (
        "You qualify one X post for an AI-agent tool-discovery outreach watcher. "
        "Return one JSON object only with keys builder, specificity, substance, total, "
        "action, reason, tool_discovery, topic. builder must be 0..50, specificity "
        "0..30, substance 0..20, and total must equal their sum. action must be "
        "comment or skip. Use comment only when the actual post is substantively about "
        "building an AI agent/assistant and discovering, selecting, routing, loading, or "
        "calling tools, including MCP tools. Generic AI, launches, hiring, crypto, and "
        "consumer use must skip. topic must be agent, assistant, or empty. Post: "
        + json.dumps(payload, sort_keys=True)
    )
    response = complete_fn(prompt, max_completion_tokens=180)
    try:
        result = json.loads(response["text"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Portkey qualification returned invalid JSON") from exc
    expected = {
        "builder",
        "specificity",
        "substance",
        "total",
        "action",
        "reason",
        "tool_discovery",
        "topic",
    }
    if set(result) != expected:
        raise RuntimeError("Portkey qualification returned an invalid schema")
    scores = (result["builder"], result["specificity"], result["substance"])
    if any(type(value) is not int for value in scores):
        raise RuntimeError("Portkey qualification scores must be integers")
    if not (0 <= scores[0] <= 50 and 0 <= scores[1] <= 30 and 0 <= scores[2] <= 20):
        raise RuntimeError("Portkey qualification score outside policy bounds")
    if type(result["total"]) is not int or result["total"] != sum(scores):
        raise RuntimeError("Portkey qualification total mismatch")
    if result["action"] not in {"comment", "skip"}:
        raise RuntimeError("Portkey qualification action invalid")
    if not isinstance(result["reason"], str) or not result["reason"].strip():
        raise RuntimeError("Portkey qualification reason missing")
    if type(result["tool_discovery"]) is not bool:
        raise RuntimeError("Portkey qualification tool flag invalid")
    if result["topic"] not in {"", "agent", "assistant"}:
        raise RuntimeError("Portkey qualification topic invalid")
    if result["action"] == "comment" and not result["tool_discovery"]:
        raise RuntimeError("Portkey comment requires tool-discovery evidence")
    return {**result, "policy": POLICY, "model": response.get("model")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--channel", default="")
    parser.add_argument("--name", default="Candidate")
    parser.add_argument("--profile-url", dest="profile_url")
    parser.add_argument("--parent-url", dest="parent_url")
    parser.add_argument("--batch", help="JSON array of posts; score in one process")
    args = parser.parse_args()
    if args.batch:
        items = json.loads(Path(args.batch).read_text() if args.batch != "-" else sys.stdin.read())
        print(json.dumps([score_post(item) for item in items], indent=2))
        return
    if not args.channel:
        parser.error("--channel is required unless --batch is set")
    profile = args.profile_url or {
        "x": "https://x.com/candidate",
        "linkedin": "https://www.linkedin.com/in/candidate",
        "hacker_news": "https://news.ycombinator.com/user?id=candidate",
        "github": "https://github.com/candidate",
        "product_hunt": "https://www.producthunt.com/@candidate",
    }.get(args.channel, "https://example.com/candidate")
    parent = args.parent_url or profile
    print(
        json.dumps(
            score_post(
                {
                    "channel": args.channel,
                    "name": args.name,
                    "profile_url": profile,
                    "parent_url": parent,
                    "text": args.text,
                    "title": args.title,
                }
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
