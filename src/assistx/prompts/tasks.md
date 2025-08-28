
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
      "confidence": number
    }
  ]
}
Guidelines: Prefer MEDIUM unless urgency/explicit deadlines suggest HIGH. Include due if explicitly stated. Confidence in [0,1].
Return only JSON.
