import asyncio
import base64
import json
from gmail_mcp.core.gmail_client import GmailClientWrapper

async def main():
    client = GmailClientWrapper()
    service = client._get_thread_service()

    msg = service.users().messages().get(userId='me', id='1a1c4bca70862f54', format='full').execute()
    payload = msg.get('payload', {})
    
    def extract_bodies(part):
        mime = part.get('mimeType')
        data = part.get('body', {}).get('data')
        bodies = {}
        if data:
            decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            bodies[mime] = decoded
        for sub in part.get('parts', []):
            bodies.update(extract_bodies(sub))
        return bodies

    bodies = extract_bodies(payload)
    print("=== TEXT/PLAIN BODY ===")
    print(bodies.get('text/plain', 'NONE'))
    print("\n=== TEXT/HTML BODY ===")
    print(bodies.get('text/html', 'NONE'))

if __name__ == '__main__':
    asyncio.run(main())
