# llm_utils.py
import os
import json
import requests
import textwrap
import re


# ——— API Configuration ———
GEMMA3_API_URL  = "https://generativelanguage.googleapis.com/v1beta/models/gemma-3-27b-it:generateContent"
# GEMMA3_MODEL    = None
GEMMA3_API_KEY  = "AIzaSyC_3sCoLKIztbgH3j7I5FkjSouwUeZGQOg"
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
        try:
            terms = json.loads(text)
        except json.JSONDecodeError:
            # fallback splitter
            cleaned = text.strip().strip("[]")
            terms = [t.strip().strip('"') 
                     for t in cleaned.split(",") if t.strip()]

        if isinstance(terms, list):
            detected.extend(terms)

    # dedupe and return
    return list(dict.fromkeys(detected))


