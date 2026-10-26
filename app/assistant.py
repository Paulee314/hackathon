"""Turn what the driver says or types into either a cash entry or an answer from their books.

One Muse Spark call decides which it is and does it. Answers may use only the numbers in the
books context that app.books computes, so the model explains numbers rather than inventing them.
"""
from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Dict

from agents.meta import chat, chat_json
from app.books import Books, money, short_date

PROMPT = """You are the money copilot inside a phone app for a gig driver. The driver {how} this to you:
"{text}"

Today is {today} ({weekday}). The driver either asked about their books, told you about cash they spent, or
asked what would have happened with a rule for which ride offers to take or skip ("what if I skip pickups over
3 miles?", "only take rides paying a dollar a mile").

BOOKS, the only numbers you may use (never invent others):
{books}

Speech recognition often drops the decimal point in spoken prices ("eight fifty" can come out as "850"). When the
driver is logging spending and a number without a decimal point would be an implausible price for what was bought,
read it as dollars and cents and say so in "assumption".

Return only JSON, no prose:
{{"intent": "log" | "question" | "rule" | "other",
 "rule": null, or for "rule": {{"name": the rule in a few of the driver's words,
    "max_pickup_miles": number or null, "min_fare": number or null, "min_fare_per_mile": number or null,
    "min_profit_per_hour": number or null (only if they mean after car costs), "platforms": ["uber" and/or "lyft"] or null,
    "hours": [first hour, last hour] in 24-hour time or null}},
 "expense": null, or for "log": {{"date": "YYYY-MM-DD" (resolve words like "yesterday" against today),
    "amount": number, "payee": string, "description": short string,
    "business_line": "rideshare" | "turo" | "personal" | "unclear",
    "schedule_c_line": "<line number> <line name>" such as "9 Car and truck expenses", or null if personal,
    "assumption": one short sentence naming any reading you made, or null,
    "question": one short question if the amount or purpose is missing, or null}},
 "answer": null, or for "question" and "other": {{"headline": the key figure or phrase, at most 6 words,
    "text": 1 to 3 short sentences in plain words, using only BOOKS; if BOOKS can't answer, say what is missing,
    "followups": up to 2 short questions the driver might ask next, worded as the driver would ask them,
    such as "What did I make in October?" (not offers like "Want your total?")}}}}

Weeks run Monday to Sunday; when the current week isn't over, say "so far"."""

EXPLAIN = """You help a gig driver review their books. Explain this review item in 2 or 3 short sentences of plain
words: what happened, and what each choice does to their books and taxes. Be accurate about US tax rules for
self-employed drivers: if a choice would not hold up (meals while driving locally are not deductible, for example),
say so plainly. No jargon, no greeting.

Item:
{item}"""


def handle(text: str, books: Books, spoken: bool) -> Dict:
    today = books.today
    prompt = PROMPT.format(how="said" if spoken else "typed", text=text.strip(), today=today.isoformat(),
                           weekday=today.strftime("%A"), books=json.dumps(books.context(), default=str))
    out = chat_json([{"role": "user", "content": prompt}])
    intent = out.get("intent")
    if intent == "log" and out.get("expense"):
        return _logged(out["expense"], text, spoken, today)
    if intent == "rule" and isinstance(out.get("rule"), dict):
        return books.rule_result(out["rule"])  # the replay and every number in the answer come from code
    answer = out.get("answer") or {"headline": "", "text": "I couldn't work that out. Try asking another way.", "followups": []}
    return {"type": "answer", "headline": answer.get("headline", ""), "text": answer.get("text", ""),
            "followups": [f for f in answer.get("followups", []) if isinstance(f, str)][:2]}


def _logged(expense: Dict, text: str, spoken: bool, today: date) -> Dict:
    try:
        amount = Decimal(str(expense.get("amount"))).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        amount = None
    if amount is None or amount <= 0:
        return {"type": "question", "text": expense.get("question") or "How much was it?"}
    if expense.get("question"):
        return {"type": "question", "text": expense["question"]}
    try:
        when = date.fromisoformat(str(expense.get("date")))
    except ValueError:
        when = today
    parsed = {"date": when.isoformat(), "amount": float(amount), "payee": expense.get("payee") or "",
              "description": expense.get("description") or "Cash expense",
              "business_line": expense.get("business_line"), "schedule_c_line": expense.get("schedule_c_line"),
              "assumption": expense.get("assumption"), "question": None}
    line = parsed["business_line"]
    if line not in ("rideshare", "turo"):
        return {"type": "noted", "headline": money(amount), "text": f"{parsed['description']}: kept out of the business books"
                + (" as personal." if line == "personal" else ". Tell me if it was for rideshare or Turo.")}
    entry = {"id": f"e{int(time.time() * 1000)}", "parsed": parsed, "transcript": text, "spoken": spoken,
             "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    sch = parsed["schedule_c_line"] or ""
    return {"type": "logged", "entry": entry, "title": parsed["description"], "amount": money(amount),
            "meta": f"Cash, {'Turo' if line == 'turo' else 'rideshare'}, {short_date(parsed['date'])}"
                    + (f", Schedule C line {sch}" if sch else ""),
            "assumption": parsed["assumption"]}


def explain(item: Dict) -> str:
    keep = {k: item[k] for k in ("tag", "title", "body", "rows", "suggestion", "choices", "found_by") if k in item}
    return chat([{"role": "user", "content": EXPLAIN.format(item=json.dumps(keep))}]).strip()
