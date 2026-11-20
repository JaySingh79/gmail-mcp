import asyncio
from gmail_mcp.core.gmail_client import GmailClientWrapper

async def main():
    client = GmailClientWrapper()
    service = client._get_thread_service()

    # Get message 195396f86924e57c
    msg = service.users().messages().get(userId='me', id='195396f86924e57c').execute()
    print("Message 195396f86924e57c:")
    print("  ThreadId:", msg.get('threadId'))
    print("  LabelIds:", msg.get('labelIds'))
    headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
    print("  Headers:", headers)
    print("  Snippet:", msg.get('snippet'))

    # Fetch all drafts by paging through drafts().list()
    all_drafts = []
    page_token = None
    while True:
        res = service.users().drafts().list(userId='me', maxResults=100, pageToken=page_token).execute()
        drafts = res.get('drafts', [])
        all_drafts.extend(drafts)
        page_token = res.get('nextPageToken')
        if not page_token:
            break

    print(f"\nTotal drafts in account: {len(all_drafts)}")

    # Check each draft
    print("\nScanning all drafts for template candidates (where To is empty or subject matches template list)...")
    template_candidates = []
    for d in all_drafts:
        d_id = d['id']
        detail = service.users().drafts().get(userId='me', id=d_id, format='metadata').execute()
        m = detail.get('message', {})
        h = {x['name']: x['value'] for x in m.get('payload', {}).get('headers', [])}
        to = h.get('To', '').strip()
        subject = h.get('Subject', '').strip()
        labels = m.get('labelIds', [])
        snippet = m.get('snippet', '')
        
        # If no recipient or matches known templates
        if not to or any(k.lower() in subject.lower() for k in ["follow up", "application for data science", "data science/ml internship"]):
            template_candidates.append({
                "draft_id": d_id,
                "message_id": m.get('id'),
                "subject": subject,
                "to": to,
                "labels": labels,
                "snippet": snippet[:60]
            })

    print(f"\nFound {len(template_candidates)} candidates:")
    for c in template_candidates:
        print(f"- Subject: '{c['subject']}' | To: '{c['to']}' | DraftID: {c['draft_id']} | MsgID: {c['message_id']}")

if __name__ == '__main__':
    asyncio.run(main())
