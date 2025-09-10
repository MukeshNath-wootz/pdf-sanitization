# llm_utils.py
import os
import json
import requests
import textwrap
import re


# ——— API Configuration ———
GEMMA3_API_URL  = "https://generativelanguage.googleapis.com/v1beta/models/gemma-3-27b-it:generateContent"
# GEMMA3_MODEL    = None
GEMMA3_API_KEY = os.getenv("GEMMA3_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
if not GEMMA3_API_KEY:
    raise RuntimeError("Set GEMMA3_API_KEY in your environment before running")

# Utility function to chunk text into smaller parts
# to avoid hitting token limits in LLMs.
def _chunk_text(text: str, max_chars: int = 2000) -> list[str]:
    """
    Naïve sentence-based chunker so each prompt stays under token limits.
    """
    sentences = re.split(r'(?<=[.?!])\s+', text)
    chunks, current = [], []
    length = 0
    for sent in sentences:
        if length + len(sent) > max_chars:
            chunks.append(" ".join(current))
            current, length = [sent], len(sent)
        else:
            current.append(sent)
            length += len(sent)
    if current:
        chunks.append(" ".join(current))
    return chunks

# Function to get sensitive terms from LLM
# This function sends the concatenated PDF text and context to Gemma 3 27B
# and returns a plain list of detected sensitive words/phrases.
def get_sensitive_terms_from_llm(
    all_text: str,
    context: str
) -> list[str]:
    """
    Calls Gemma 3 in chunks, then returns a deduped list
    of newly detected sensitive terms.
    """
    # if someone passed a list of text pieces, join them for you
    if isinstance(all_text, (list, tuple)):
        all_text = "\n".join(all_text)

    detected = []
    for chunk in _chunk_text(all_text):
        prompt = textwrap.dedent(f"""
            Context:
            {context}

            Below is a slice of the text extracted from a manufacturing-drawing PDF.
            Only return a JSON array of the phrases that are SENSITIVE
            (e.g. personal names, emails, phone numbers, addresses, account codes).

            Text:
            \"\"\"
            {chunk}
            \"\"\"

            Output format:
            ["term1", "term2", ...]
        """).strip()

        # new: send your API key as an X-Goog-Api-Key header
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GEMMA3_API_KEY
        }
        # use the Google-approved JSON shape:
        
        payload = {
            "contents": [
                { "parts": [{ "text": prompt }] }
            ],
            "generationConfig": {
                "temperature":   0.0,
                "maxOutputTokens": 1024
            }
        }
        # payload = {
        #     "model":       GEMMA3_MODEL,
        #     "prompt":      prompt,
        #     "max_tokens":  1024,
        #     "temperature": 0.0,
        # }
        resp = requests.post(GEMMA3_API_URL, headers=headers, json=payload)
        resp.raise_for_status()

        js   = resp.json()
        text = js["candidates"][0]["content"]["parts"][0]["text"]

        # Normalize common wrappers like ```json ... ```, leading 'json' labels, etc.
        raw = (text or "").strip()
        # Strip markdown code fences ```json ... ``` or ``` ... ```
        if raw.startswith("```"):
            raw = raw.strip("`")  # remove backticks
            # Sometimes leading language label remains like json[ ...
        # Remove a leading language tag like 'json' or 'JSON' before the array
        raw = re.sub(r"^\s*(?i:json)\s*", "", raw)
        # If there's surrounding text, try to extract the first JSON array
        m = re.search(r"\[.*\]", raw, flags=re.S)
        candidate = m.group(0) if m else raw

        # Try strict JSON parse first
        terms: list[str] = []
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                terms = [str(x) for x in parsed]
            else:
                # If the model returned an object with a key like terms, try to pull it out
                if isinstance(parsed, dict):
                    maybe = parsed.get("terms") or parsed.get("sensitive_terms")
                    if isinstance(maybe, list):
                        terms = [str(x) for x in maybe]
        except Exception:
            # Fallback: split by commas inside the bracketed section
            cleaned = candidate.strip()
            cleaned = cleaned.strip().lstrip("json").lstrip("JSON").strip()
            cleaned = cleaned.strip().strip("[]")
            parts = [p for p in cleaned.split(",") if p.strip()]
            terms = [p.strip().strip('"').strip("'").strip() for p in parts]

        if isinstance(terms, list):
            # Final per-term normalization to remove any lingering quotes/backticks
            normed = []
            for t in terms:
                if t is None:
                    continue
                s = str(t).strip()
                # remove surrounding quotes repeatedly
                while (len(s) >= 2) and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
                    s = s[1:-1].strip()
                # trim leftover backticks/spaces
                s = s.strip("` ")
                if s:
                    normed.append(s)
            detected.extend(normed)

    # dedupe and return
    return list(dict.fromkeys(detected))


