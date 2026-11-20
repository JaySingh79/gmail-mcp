import asyncio
import json
from gmail_mcp.core.gmail_client import GmailClientWrapper

async def check():
    client = GmailClientWrapper()
    service = client._get_thread_service()
    
    # 1. Check Follow Up Corp
    msg = service.users().messages().get(userId='me', id='19a5425a867ba2e0', format='full').execute()
    print("=== MSG 19a5425a867ba2e0 (Follow Up Corp) ===")
    print("Labels:", msg.get('labelIds'))
    for h in msg.get('payload', {}).get('headers', []):
        print(f"  {h['name']}: {h['value']}")
        
    draft = service.users().drafts().get(userId='me', id='r1927854516532133307', format='full').execute()
    print("\nDraft keys:", list(draft.keys()))
    print("Draft id:", draft.get('id'))

if __name__ == '__main__':
    asyncio.run(check())
