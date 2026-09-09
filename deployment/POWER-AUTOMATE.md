# Configure delivery and monitoring in Power Automate

Use the firm's approved Power Platform environment. These instructions use
SharePoint and Office 365 Outlook connectors. Set test recipients first. Build
the flows in a solution if the firm uses solution-based lifecycle management, then
export the completed solution with its connection references for backup.

The repository's JSON schema is for **Parse JSON**. It is not a complete importable
flow. The mailbox/site/connection choices must be bound in your tenant.

## A. Create the delivery log

In the selected SharePoint site choose **New → List → Blank list**. Name:
**DoTDeliveryLog**. Use these exact names when first creating columns, so their
internal names match the expressions below. Do not rename the built-in Title column.

| Column | Type | Configuration |
| --- | --- | --- |
| Title | Existing single line of text | Required; **Enforce unique values = Yes** in List settings → Columns → Title |
| ReportingMonth | Single line of text | Required; index this column |
| Status | Choice | `Sending`, `Sent`, `NeedsReview`; required |
| PackageUrl | Single line of text | Folder URL, optional; use multiple lines plain text if your site's URLs exceed 255 chars |
| RunUrl | Single line of text | GitHub run URL, optional |
| StartedAt | Date and time | Include time |
| SentAt | Date and time | Include time; optional |
| Details | Multiple lines of text | Plain text |

Title stores the package key, not the email subject. Restrict list editing to the
flow account and support owners. Stakeholders need read access to the documents;
they do not need permission to edit outbox or delivery-log records.

## B. Create the delivery flow

1. **Create → Scheduled cloud flow**. Name **DoT — deliver ready packages**.
2. Repeat every **30 minutes**. This polls SharePoint for completed release records,
   including any created while the flow was off.
3. In the Recurrence trigger's **Settings**, turn concurrency control **On**, degree
   of parallelism **1**. Keep loops sequential as well.
4. Add the actions below, renaming them exactly as shown before adding expressions.
   Action names in expressions use underscores. Insert expressions through the
   Expression tab without a leading `@`; HTML examples use `@{...}` inline.

### 1 — Get_ready_files (SharePoint: Get files (properties only))

- Site Address: chosen site.
- Library Name: chosen document library.
- Limit Entries to Folder: select **DoT Collector/outbox** with the folder picker.
- Include Nested Items: **No**.
- Filter Query: `FSObjType eq 0`.
- Settings → Pagination: **On**, threshold **5000**.

Only the collector's JSON completion records belong in this folder. Keep the
outbox and delivery-log records paired when archiving older history. Monitor the
pagination threshold as history grows; do not delete the ledger while leaving
its outbox records available for polling.

### 2 — For_each_package (Control: Apply to each)

Input: **value** from Get_ready_files. Leave loop concurrency off. Put the remaining
delivery actions inside this loop.

### 3 — Get_file_content (SharePoint: Get file content)

- Site: same site.
- File Identifier: **Identifier** from the current Get_ready_files item.

### 4 — Parse_package (Data operations: Parse JSON)

- Content: select **File Content** from Get_file_content.
- Schema: paste the entire contents of **ready-package.schema.json**.

The File Content dynamic token normally supplies JSON correctly. If your connector
returns a binary wrapper and the pilot reports an object/schema error, inspect the
Get_file_content output. When the body contains `$content` with base64 data, use:

```text
json(base64ToString(body('Get_file_content')?['$content']))
```

Use that expression only for a confirmed `$content` wrapper. Do not base64-decode
already-parsed JSON. Never paste a downloaded order URL into an HTTP action here.

### 5 — Is_ready (Condition)

Expression:

```text
and(equals(body('Parse_package')?['schema_version'], 1), equals(body('Parse_package')?['status'], 'Ready'))
```

If false, do not send. Flag the malformed marker to the process owner and leave it
for investigation. Put the following actions in the **Yes** branch.

### 6 — Find_delivery (SharePoint: Get items)

- List: **DoTDeliveryLog**.
- Filter Query: use this Expression:

```text
concat('Title eq ''', body('Parse_package')?['package_key'], '''')
```

- Top Count: **1**. Title's unique-value constraint creates its index.

### 7 — Not_previously_claimed (Condition)

```text
equals(length(body('Find_delivery')?['value']), 0)
```

If false, **do nothing regardless of the existing status**. A row in Sending or
NeedsReview may represent an email already accepted by Outlook. Do not automatically
resend it. The monitor and recovery procedure handle this case.

In the **Yes** branch add:

### 8 — Claim_delivery (SharePoint: Create item)

| Field | Value/expression |
| --- | --- |
| Title | `body('Parse_package')?['package_key']` |
| ReportingMonth | `body('Parse_package')?['period']` |
| Status | `Sending` |
| PackageUrl | `body('Parse_package')?['folder_url']` |
| RunUrl | `body('Parse_package')?['run_url']` |
| StartedAt | `utcNow()` |
| Details | `Delivery claimed; investigate before retrying if it does not become Sent.` |

Use the Expression tab for expressions. **All email actions must run only after
this Create item succeeds.** Keep default “is successful” run-after on the next
scope. If two flows race, the unique Title blocks the second claim. Do not add a
failure path that proceeds to send anyway.

### 9 — Send_and_record (Control: Scope)

Inside the scope add these actions in order:

**Send_package — Office 365 Outlook: Send an email from a shared mailbox (V2)**

- Original Mailbox Address: the firm-approved shared mailbox.
- To: the test recipient initially, then the stakeholder distribution group.
- Subject: Expression:

```text
concat('DoT blocking orders — ', body('Parse_package')?['label'], ' — ', substring(body('Parse_package')?['package_key'], 8, 12))
```

- Body: switch to HTML/code view and paste **email-body.html**.
- Do not attach the large ZIP; the body links directly to both documents.
- **Settings → Retry policy → None**. A send that times out might still have been
  accepted. We hold uncertain cases rather than automatically retrying email.

The connection account must have the appropriate shared-mailbox permission. A
shared mailbox address is not itself a login/connection identity. Have Exchange
administrators confirm where sent messages are retained for investigation.

**Mark_sent — SharePoint: Update item**

- List: DoTDeliveryLog.
- ID: **ID from Claim_delivery**, not a document identifier.
- Title and ReportingMonth: same expressions used in Claim_delivery.
- Status: `Sent`.
- StartedAt: `body('Claim_delivery')?['StartedAt']`.
- SentAt: `utcNow()`.
- PackageUrl and RunUrl: same expressions as Claim_delivery.
- Details: `Outlook send action succeeded.`

Keep the default successful run-after from Send_package to Mark_sent. SharePoint
updates may retain their normal retry policy; they do not send another email.

### 10 — Hold_uncertain (Control: Scope, following Send_and_record)

Configure run-after on this scope: select **has failed** and **has timed out** for
Send_and_record; deselect **is successful** and **is skipped**.

Inside add **Update item** for the Claim_delivery ID with the same required fields,
Status `NeedsReview`, and Details:

```text
Email or subsequent log update failed/timed out. Check the flow run and mailbox before retrying.
```

Then add an Outlook email to the **process owner**, with the reporting month,
package key, and instruction to inspect the failed flow run. This is a support
alert, not the stakeholder package email. If the connection itself is broken,
this alert may also fail; configure Power Automate's native failure notifications
and the independent monitor below.

Save the flow. Turn it on only with test recipients configured. Run a test after
the pilot marker exists. Check Outlook and the Sent row, then run it again and
confirm that the existing claim suppresses another send.

## C. Create an independent monthly-delivery monitor

Create another scheduled cloud flow: **DoT — missing monthly delivery**.

1. Recurrence: once daily at **10:00**, timezone **India Standard Time**. Set
   trigger concurrency to 1. Do not place this monitor inside the collector's job.
2. Compose action **India_now**:

```text
convertTimeZone(utcNow(), 'UTC', 'India Standard Time')
```

3. Compose action **Expected_month**:

```text
formatDateTime(addToTime(startOfMonth(outputs('India_now')), -1, 'Month'), 'yyyy-MM')
```

4. Condition **Past_delivery_deadline**:

```text
greaterOrEquals(dayOfMonth(outputs('India_now')), 6)
```

5. In Yes, **Find_sent_month** (SharePoint Get items) on DoTDeliveryLog, Top Count 1,
   Filter Query expression:

```text
concat('ReportingMonth eq ''', outputs('Expected_month'), ''' and Status eq ''Sent''')
```

6. If `equals(length(body('Find_sent_month')?['value']), 0)`, send an alert to the
   process owner: “No successful DoT package delivery recorded for [Expected_month].
   Check GitHub Actions, SharePoint outbox and the delivery flow.” Otherwise do nothing.
   This alerts daily from the sixth until a Sent entry exists or the month changes.
7. Also add **Find_unresolved** (Get items, pagination On) with filter:
   `Status ne 'Sent'`. Loop sequentially through these records; if StartedAt is
   more than two hours old, alert the owner with the Title/status and PackageUrl.
   Suggested condition inside the loop (rename it For_each_unresolved):

```text
less(ticks(items('For_each_unresolved')?['StartedAt']), ticks(addHours(utcNow(), -2)))
```

Run this unresolved check every day, outside the Past_delivery_deadline condition.
It catches stuck claims and failed revised packages even when an earlier version
of the month was successfully sent. Add native failure notifications for this
monitor itself and a backup owner who can repair expired connections.

## D. Recovery without accidental duplicate mail

- **No delivery-log row:** repair the parsing/access error. The next poll can claim
  the valid marker. Inspect failed runs; the missing-delivery monitor is the fallback.
- **Sending or NeedsReview:** inspect the delivery flow run and the sender's sent
  items or Exchange message trace. If the email was accepted, set the row to Sent
  and document the evidence. If confirmed not sent, turn off the delivery flow,
  remove only that claim row, then turn it on for one controlled retry.
- **Sent, but a recipient cannot open links:** fix SharePoint permissions; another
  copy of the same package does not solve access rights.
- **Intentional new version:** rebuild with changed contents or a deliberate new
  revision label. It gets its own package key and delivery record.

There is no atomic transaction across Outlook and SharePoint. This design prevents
normal duplicate sends and deliberately requires investigation of uncertain sends.
It does not promise exactly-once delivery or prove receipt by every stakeholder.

## E. Back up the tenant configuration

After successful testing, use Power Automate's solution export (with environment
variables/connection references if used by the firm). Keep the export in the firm's
approved deployment storage. Record the flow names, site/list/library, connection
owner, mailbox, recipients, backup owner and licensing allocation. The repository
source ZIP is not a substitute for the configured tenant flow export.

References: [SharePoint connector](https://learn.microsoft.com/en-us/connectors/sharepointonline/),
[Outlook connector](https://learn.microsoft.com/en-us/connectors/office365/),
[Power Automate concurrency](https://learn.microsoft.com/en-us/power-automate/guidance/coding-guidelines/implement-parallel-execution),
[connection recovery](https://learn.microsoft.com/en-us/power-automate/fix-connection-failures).
