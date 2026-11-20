The primary bottleneck appears to be the handling of large JSON responses and reliance on external file I/O within the system. External file I/O
  increases latency. Re-evaluating data flow and in-memory processing is necessary to reduce tool timeouts and excessive output file sizes.


  The system lacks dedicated tools to directly send created drafts, requiring a workaround that rebuilds the bulk send plan. Adding tools to send
  individual and bulk drafts, leveraging the Gmail API's send function, is proposed. This avoids unnecessary re-uploading and MIME rebuilding.

  Output truncation and compact mode for bulk send preview dumps should be implemented to address excessive tool output.


  The excessive size of JSON outputs from several functions leads to file spillage and subsequent `read_file` operations, which impacts performance.
  Implementing a `compact` mode will trim these responses, returning only essential information like status and counts, which solves the tool output issue.
  Expanding template functionality will enable direct extraction of first names and other data manipulations within templates. This simplifies data
  preparation and reduces external dependencies.


  Allow dynamic workspace paths to eliminate forced file copying steps and `PATH_OUTSIDE_ALLOWED_DIRS` errors. This can be addressed by including the
  client's working directory or project root as allowed attachment paths, or allowing configuration of allowed directories.

  Direct source data parsing requires incorporating field mapping. This allows `gmail_mcp` to handle any JSON data source without intermediate batch
  scripts. Implementation of a `field_mapping` argument within the tool configuration will enable direct field mapping from the source data to the tool's
  expected schema.

  To prevent 3-minute tool timeouts, chunking the batch send and asynchronous task execution will be implemented. This avoids single synchronous calls that
  exceed time limits, ensuring emails with attachments can be sent without errors.
  Here is a detailed, architect-level breakdown of the technical friction points in gmail_mcp and the exact improvements needed so all outreach
  operations happen cleanly and natively in a single step without external scripts or file dumps in brain:
  ──────
  ### 1. Missing Native Draft-Sending Tool (gmail_send_draft & gmail_bulk_send_drafts)

  • The Problem: gmail_mcp provides gmail_create_draft and gmail_bulk_create_drafts, but zero tools to send existing drafts. When you instructed "send
  all", the 51 staged drafts in your Drafts folder could not be dispatched by their draft_id. Instead, the system had to rebuild MIME messages and
  execute a completely separate bulk-send workflow.
  • The Improvement Needed:
      • Add gmail_send_draft(draft_id: str): Calls the native Gmail API users.drafts.send.
      • Add gmail_bulk_send_drafts(draft_ids: list[str]): Takes a list of draft IDs and dispatches them in one call without re-rendering bodies, re-
      checking variables, or re-uploading PDFs.

  ──────
  ### 2. Huge Payload Bloat Forcing Disk Spills (output.txt in Brain)

  • The Problem: Whenever gmail_prepare_bulk_send, gmail_execute_bulk_send, or gmail_search_messages run on 20+ recipients, the tool returns a massive
  JSON payload containing raw email headers, full MIME previews, base64 metadata, and itemized results. Because the response exceeds the agent's single-
  turn token/character limit, the platform automatically dumps it into:
  C:\Users\binit\.gemini\antigravity-cli\brain\<id>\.system_generated\steps\<step>\output.txt
  This forced secondary read_file calls just to get a plan_id or check sent status.
  • The Improvement Needed:
      • Add a compact: bool = True (or summary_only: bool = True) parameter across all bulk tools.
      • In compact mode, return strictly operational metadata:
        {
          "success": true,
          "plan_id": "plan_805954b35ed0",
          "count": 51,
          "required_confirmation_phrase": "CONFIRM SEND 51 EMAILS",
          "status": "PREPARED"
        }
      This completely eliminates multi-thousand-line JSON blobs and disk spillover.

  ──────
  ### 3. Rigid Attachment Directory Sandboxing (PATH_OUTSIDE_ALLOWED_DIRS)

  • The Problem: gmail_mcp hardcodes allowed attachment directories to D:\Projects\gmail_mcp\attachments. Any file generated inside the active project
  workspace (D:\Projects\PRACTICE\firecrawl_test\Resumes\cover_letter\...) gets rejected with:
  PATH_OUTSIDE_ALLOWED_DIRS: Path '...' resolves outside configured allowed directories.
  This forced running PowerShell scripts to copy PDFs between project folders.
  • The Improvement Needed:
      • Allow dynamic workspace paths in gmail_mcp's .env or config:
      ALLOWED_ATTACHMENT_DIRS="D:\Projects\PRACTICE\firecrawl_test\Resumes;attachments"
      • Or automatically resolve relative paths against the calling client’s workspace root.

  ──────
  ### 4. Template Engine Lacks Filters ({{name | first_name}})

  • The Problem: The SQLite template engine only performs raw string replacement: {{recipient_name}}. It cannot manipulate strings. When candidate leads
  contain "Ajaay Kiran" or "Subhashis Dash", the agent was forced to write a Python script in scratch/ to split names before passing them to the tool.
  • The Improvement Needed:
      • Add Jinja2-style filters or built-in transforms in template interpolation:
          • {{recipient_name | first_name}} → automatically extracts "Ajaay" from "Ajaay Kiran".
          • {{recipient_name | title}} → proper capitalization.
      • Or add a boolean flag to draft/send tools: extract_first_name: bool = True.

  ──────
  ### 5. No Direct Ingestion of Raw Scraped JSON Files (field_mapping)

  • The Problem: Lead harvest files (like target_14_startups_hr.json) use custom schemas:
  {"company": "Navi", "name": "Ajaay Kiran", "top_candidate_email": "..."}
  Because gmail_mcp strictly requires {"to": "...", "recipient_name": "...", "attachments": [...]}, the agent had to write a Python script to reshape the
  JSON data.
  • The Improvement Needed:
      • Add a field_mapping parameter to gmail_bulk_create_drafts and gmail_prepare_bulk_send:
        {
          "recipient_source": "D:/Projects/PRACTICE/firecrawl_test/src/.../target_14_startups_hr.json",
          "template_id": "job_application_data_science",
          "field_mapping": {
            "to": "top_candidate_email",
            "recipient_name": "name",
            "company": "company"
          },
          "attachment_pattern": "D:/Projects/.../cover_letter_{{company}}.pdf"
        }

      • This would allow feeding any scraped JSON file directly into the tool with zero intermediate Python scripts.

  ──────
  ### 6. Synchronous Loop Causes 3-Minute Hard Timeouts

  • The Problem: Sending 51 emails sequentially with two PDF attachments over Google's REST API took ~200 seconds (~3.5 to 4 seconds per email). The
  client tool call has a strict 180-second (3m0s) socket timeout. When the timeout triggered, the client canceled the connection while the background
  server thread kept running invisibly, creating state desynchronization.
  • The Improvement Needed:
      • Asynchronous Concurrency: Use asyncio.gather or a ThreadPoolExecutor(max_workers=5) to send 5 emails in parallel. 50 emails would take ~35
      seconds instead of 200+ seconds, easily beating the 3-minute timeout.
      • Background Job Pattern: For large batches (>25 emails), gmail_execute_bulk_send should immediately return {"job_id": "job_123", "status":
      "RUNNING"} with a lightweight status checker gmail_get_bulk_status(job_id) instead of blocking the tool call.