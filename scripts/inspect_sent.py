import asyncio
import base64
from gmail_mcp.core.gmail_client import GmailClientWrapper

async def main():
    client = GmailClientWrapper()
    service = client._get_thread_service()

    # Get the latest sent emails
    res = service.users().messages().list(userId='me', q='label:SENT', maxResults=5).execute()
    msgs = res.get('messages', [])
    print(f"Found {len(msgs)} sent messages.")

    for idx, m in enumerate(msgs):
        msg = service.users().messages().get(userId='me', id=m['id'], format='full').execute()
        headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
        print(f"\n--- SENT EMAIL #{idx+1} ---")
        print(f"ID: {m['id']}")
        print(f"Date: {headers.get('Date')}")
        print(f"To: {headers.get('To')}")
        print(f"Subject: {headers.get('Subject')}")
        print(f"Snippet: {msg.get('snippet')}")
        
        # Check parts / attachments
        payload = msg.get('payload', {})
        parts = payload.get('parts', [])
        print(f"Parts count: {len(parts)}")
        for p in parts:
            filename = p.get('filename')
            mimeType = p.get('mimeType')
            body = p.get('body', {})
            att_id = body.get('attachmentId')
            size = body.get('size')
            print(f"  Part: filename='{filename}', mimeType='{mimeType}', size={size}, att_id={bool(att_id)}")

if __name__ == '__main__':
    asyncio.run(main())
