
From the conversation summary and bullets, extract ACTION ITEMS strictly as JSON with this schema:
{
  "summary": string,
  "bullets": string[],
  "tasks": [
    {
      "title": string,
      "description": string,
      "priority": "LOW"|"MEDIUM"|"HIGH",
      "due": string|null,
      "confidence": number,
      "acceptance": [
        {
          "type": "file_exists" | "contains" | "regex" | "http_ok",
          "args": { "path"?: string, "text"?: string, "pattern"?: string, "url"?: string }
        }
      ]
    }
  ]
}
Guidelines: Prefer MEDIUM unless urgency/explicit deadlines suggest HIGH. Include due if explicitly stated. Confidence in [0,1].
Return only JSON.

IMPORTANT: **ACCEPTANCE IS REQUIRED WHENEVER POSSIBLE.**
For each task, propose concrete acceptance checks (file_exists / contains / regex / http_ok) so completion can be objectively verified.
Prefer placing outputs under `artifacts/{TASK_ID}/...` for easy auditing.
