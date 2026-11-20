import asyncio
from gmail_mcp.core.gmail_client import GmailClientWrapper

async def main():
    client = GmailClientWrapper()
    service = client._get_thread_service()

    print("=== 1. ALL LABELS ===")
    labels_res = service.users().labels().list(userId='me').execute()
    labels = labels_res.get('labels', [])
    for l in labels:
        name = l['name']
        lid = l['id']
        ltype = l.get('type')
        if any(w in name.lower() for w in ['template', 'canned', 'draft', 'response', '^']):
            print(f"Label: {name} | ID: {lid} | Type: {ltype}")

    print("\n=== 2. SEARCH FOR SPECIFIC TEMPLATE SUBJECTS IN MESSAGES ===")
    test_subjects = ["Follow up 3", "Follow Up Corp", "Application for Data Science"]
    for subj in test_subjects:
        res = service.users().messages().list(userId='me', q=f'subject:"{subj}"', maxResults=5).execute()
        msgs = res.get('messages', [])
        print(f"Query subject:'{subj}': found {len(msgs)} messages")
        for m in msgs:
            full = service.users().messages().get(
                userId='me',
                id=m['id'],
                format='metadata',
                metadataHeaders=['Subject', 'To', 'From']
            ).execute()
            headers = {h['name']: h['value'] for h in full.get('payload', {}).get('headers', [])}
            print(f"  ID: {m['id']} | Labels: {full.get('labelIds')} | Headers: {headers}")

    print("\n=== 3. SEARCH DRAFTS ===")
    drafts_res = service.users().drafts().list(userId='me', maxResults=100).execute()
    drafts = drafts_res.get('drafts', [])
    print(f"Total drafts fetched in first page: {len(drafts)}")
    
    # Check drafts headers
    matching_drafts = []
    for d in drafts:
        d_id = d['id']
        draft_detail = service.users().drafts().get(userId='me', id=d_id, format='metadata').execute()
        msg = draft_detail.get('message', {})
        headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
        subj = headers.get('Subject', '')
        labels = msg.get('labelIds', [])
        to = headers.get('To', '')
        if any(ts.lower() in subj.lower() for ts in ["follow up", "internship", "data science"]):
            matching_drafts.append((d_id, subj, to, labels))
            print(f"Draft ID: {d_id} | Labels: {labels} | Subject: '{subj}' | To: '{to}'")

    print(f"\nTotal matching drafts found: {len(matching_drafts)}")

if __name__ == '__main__':
    asyncio.run(main())
