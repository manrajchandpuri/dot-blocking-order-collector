# Deploy the DoT collector with GitHub and Microsoft 365

This is the step-by-step deployment guide for the supplied files. It targets a
**private repository on GitHub.com**, GitHub-hosted Ubuntu runners, commercial
Microsoft 365, SharePoint Online and Power Automate cloud flows. GitHub Enterprise
Server and sovereign Microsoft clouds need adaptations.

The application and deployment files are prepared locally. A repository, Microsoft
identity, SharePoint configuration and Power Automate connections must still be
created in the firm's accounts. No Microsoft passwords belong in the repository.

## 1. Decide who will own the process

Arrange the following with IT before enabling publication:

| Item | Decision to record |
| --- | --- |
| GitHub owner | Firm organisation, with permission to create a private repository |
| Repository | Suggested name: `dot-blocking-order-collector` |
| Technical owners | Primary and backup, with access to Actions failures |
| Microsoft administrator | Can register an app, grant Graph application permissions and SharePoint site access |
| SharePoint | Site URL, document library, stakeholder read access |
| Mailbox | Shared mailbox and a licensed connection account authorised to send from it |
| Flow owner | Firm-managed licensed account, backup co-owner, approved Power Platform environment |
| Recipients | Test address first; production distribution group later |
| Timing | Fifth of each month, 09:17 India time, previous calendar month |
| Revisions | Source checked as of collection time; late publication handled by a manual rebuild |
| Retention | Firm policy for SharePoint records; GitHub troubleshooting artifacts kept 14 days |

There is no Azure compute resource in this design. The Entra application is an
identity, not an Azure server. IT must confirm GitHub Actions availability/minute
allowance, environment support for private repositories, and Power Automate
licensing/connector policies. The flows below use SharePoint and Outlook connectors;
there is no premium HTTP action or unattended desktop machine in this design.

## 2. Create the private repository and upload exactly these files

Use `dist/dot-blocking-order-collector-github.zip`. Extract it first. Upload its
**contents**, not the ZIP file itself and not a parent folder around the contents.
The package includes the entire application, fonts, tests, workflows and this guide.
It excludes downloaded orders, the local archive, credentials and the virtual environment.
`dist/upload-manifest.json` lists every included source file and its SHA-256 hash.

### Recommended: GitHub Desktop (includes hidden workflow files)

1. Install/open GitHub Desktop and sign in with the firm-approved GitHub account.
2. Choose **File → New repository**. Name: `dot-blocking-order-collector`.
3. Choose an empty local destination. Leave generated README, licence and ignore
   templates unselected; the supplied package provides the relevant files.
4. Create the repository and open its folder in Finder/File Explorer.
5. Extract the supplied ZIP into that folder. Files such as `README.md`, `src/`
   and `.github/` must be directly inside the repository folder.
6. On macOS press **Command–Shift–.** to show hidden files when copying. Confirm
   `.github/workflows/monthly-collector.yml` and `.gitignore` were copied.
7. In GitHub Desktop, inspect Changes. You should see source files, not
   `workspace/`, `.venv/`, generated ZIPs or documents.
8. Commit with summary **Prepare monthly collector deployment**.
9. Choose **Publish repository**, select the firm organisation, and leave
   **Keep this code private** selected. Publish.
10. Open the repository on GitHub. Confirm the **Private** badge and that the
    default branch is `main`. If Desktop created another branch name, rename it
    to `main` before continuing.

The initial upload triggers the **Tests** workflow. It does not publish a monthly
package. The monthly schedule is disabled until its repository variable is enabled.

### IT alternative: an empty private repository plus Git

Create an empty private repository in the correct organisation on GitHub, without
initialising a README, licence or `.gitignore`. In a terminal in the extracted
package folder, replace `FIRM` with the actual owner:

```bash
git init -b main
git add .
git status --short
git commit -m "Prepare monthly collector deployment"
git remote add origin https://github.com/FIRM/dot-blocking-order-collector.git
git push -u origin main
```

Use the firm's Git credential manager/SSO. Do not put an access token in the remote
URL. A credential used for pushing workflows must be permitted to update workflow
files. If the repository already has commits, clone it and copy the package into
the clone instead; do not force-push over it.

## 3. Create the GitHub environment and leave scheduling off

1. Repository **Settings → Environments → New environment**.
2. Name it exactly **`microsoft-production`**.
3. Under deployment branches/tags, select **Selected branches and tags**, add
   branch `main`, and permit no other branches or tags.
4. Do not require a human reviewer for every run if the final process must be
   unattended. Use code review/branch protection for changes instead. IT should
   apply the firm's normal repository controls.
5. **Settings → Secrets and variables → Actions → Variables → New repository variable**:
   add `AUTOMATION_ENABLED` with value `false`.
6. In the `microsoft-production` environment, add environment variable
   `PUBLISH_ENABLED` with value `false`.

`AUTOMATION_ENABLED` must be a **repository** variable: the job checks it before
entering the environment. The other deployment settings are environment variables.
All boolean values here are the lowercase strings `true` and `false`.

If Settings does not offer environments for this private repository, stop here
and have IT enable a supported plan/organisation configuration. Do not remove the
environment restriction without also redesigning the federated identity subject.

## 4. Have IT prepare SharePoint and the Microsoft identity

1. Create or choose a SharePoint site and document library. Give the stakeholder
   group read access and the flow connection account the required edit access.
   Use a dedicated site if access should be isolated from unrelated firm files:
   `Sites.Selected` below grants access at the selected **site** level.
2. The provided script creates a **DoT Collector** folder in that library.
3. On an administrator's machine, open PowerShell 7. Install Microsoft's module
   if it is not already available:

```powershell
Install-Module Microsoft.Graph.Authentication -Scope CurrentUser
```

4. Run the script from the repository root, replacing every example value:

```powershell
./deployment/setup-microsoft.ps1 `
  -TenantId 'YOUR-TENANT-GUID' `
  -GitHubOwner 'YOUR-FIRM-ORGANISATION' `
  -GitHubRepository 'dot-blocking-order-collector' `
  -SharePointHost 'YOUR-FIRM.sharepoint.com' `
  -SitePath '/sites/YOUR-SITE' `
  -LibraryName 'Documents'
```

`LibraryName` is the actual display name in your site; do not assume it is
`Documents`. The site path excludes the hostname. The administrator signs in
interactively. Consent requires an appropriately privileged administrator; ordinary
SharePoint ownership alone cannot grant Graph application permissions.

The script creates an application and service principal, grants **Microsoft Graph
application `Sites.Selected`**, grants **write** access on the chosen site, and
configures a GitHub federated credential:

```text
Issuer:   https://token.actions.githubusercontent.com
Audience: api://AzureADTokenExchange
Subject:  repo:YOUR-FIRM-ORGANISATION/dot-blocking-order-collector:environment:microsoft-production
```

The owner/repository spelling must match GitHub exactly. Save the application
client ID when the script prints it. If a later setup step fails, rerun the script
with the same arguments and `-AppClientId 'THE-EXISTING-CLIENT-ID'` to reuse the app.
Do not rerun without that argument unless intentionally creating another app.

The setup operator temporarily uses broader delegated administrative permissions
to create these grants. The collector's runtime application receives only the
selected-site permission; it receives **no mail permission**. The script prints
IDs, not passwords or tokens, and disconnects its administrator session.

5. Copy the printed values into **Settings → Environments → microsoft-production
   → Environment variables**:

| Variable | Value |
| --- | --- |
| `AZURE_TENANT_ID` | Printed tenant GUID |
| `AZURE_CLIENT_ID` | Printed application client ID; not the object ID |
| `SHAREPOINT_DRIVE_ID` | Printed document-library drive ID |
| `SHAREPOINT_ROOT_FOLDER_ID` | Printed DoT Collector folder item ID |
| `PUBLISH_ENABLED` | Keep `false` initially |

No client secret is needed. The code requests a short-lived GitHub identity token
and exchanges it directly for a Microsoft Graph access token. An Azure subscription
ID and `azure/login` step are not needed for this Graph-only exchange.

## 5. Run a preview on GitHub

1. Open **Actions → Tests**. Confirm the initial source checks passed.
2. Open **Actions → Monthly DoT collector → Run workflow**.
3. Select `main`; enter `2026-08` as a known reference month, or another agreed
   historical month. Leave **publish** unchecked and revision as `original`.
4. Run the workflow. It installs Python and OCR, downloads source PDFs, builds
   the package and checks it. It can take time, especially for scanned orders.
5. Open the completed run and download the `dot-collector-...` artifact.
   Extract this outer troubleshooting archive to get the actual monthly ZIP,
   Word document, QA reports, `package.json` and `run-summary.json`.
6. Confirm filenames, counts, order dates and representative extracted URLs
   against the existing local reference. For August 2026 the local README records
   22 orders and 1,366 URLs; confirm whether the live source has changed.
7. A valid preview reports `Preview`. A partial collection, invalid file, unknown
   covering-letter date, no orders, or uncorroborated extraction stops release.
   Inspect the summary/QA report when it reports `NeedsReview` or `Failed`.

Preview still downloads from DoT. It does not contact Microsoft or send email.
The runtime starts with a fresh workspace and redownloads candidate PDFs. Source
completeness does not depend on a GitHub cache surviving between monthly runs.
The familiar local app/archive is not hosted by this deployment.

## 6. Publish one pilot package to SharePoint

1. Keep the Power Automate delivery flow off until test recipients are configured.
2. Change environment variable `PUBLISH_ENABLED` to `true`.
3. Manually run the same month again, this time checking **publish**.
4. A successful run reports `Published`. Open the SharePoint DoT Collector folder:

```text
DoT Collector/
  packages/
    2026-08/
      <content-version>-<upload-attempt>/
        Blocking Orders_August 2026.zip
        Blocking Orders_August 2026.docx
        Blocking Orders_August 2026 - QA report.csv
        Blocking Orders_August 2026 - QA report.html
  outbox/
    2026-08-<content-fingerprint>.json
```

The outbox JSON is the release signal. It is written after all four output files
are uploaded and their returned sizes checked. It includes the direct SharePoint
URLs; it does not create anonymous sharing links. Recipients need existing access.
Large ZIPs use sequential Microsoft Graph upload chunks.

An interrupted upload may leave an incomplete attempt folder under `packages`.
That folder has no outbox signal and must not be sent. A rerun creates a fresh
attempt. Identical published contents reuse the existing outbox record and files.

## 7. Build the Power Automate delivery flow

Follow **[POWER-AUTOMATE.md](POWER-AUTOMATE.md)** exactly. It includes:

- The SharePoint delivery-log list and column names.
- Every flow action and the expressions to paste.
- The ready-package JSON schema and copyable email body.
- Duplicate suppression and handling of uncertain email delivery.
- A second, independent missing-delivery monitor.

The supplied schema and expressions are configuration aids, **not an importable
Power Automate solution ZIP**. A genuine solution export requires tenant-specific
connection references. Build and test these two flows in the firm's environment,
then export them as a solution through Power Automate for the firm's backup process.
Do not upload the GitHub source ZIP into Power Automate's Import screen.

## 8. Finish the pilot and enable the monthly schedule

1. Run delivery to the test recipient and confirm both SharePoint links work.
2. Poll again and rerun the same published month: there should be no second email
   and no second outbox record for identical content.
3. Confirm the failure/uncertain-delivery route with a controlled test recipient
   or test connection, not by disrupting a production mailbox.
4. Test the missing-delivery monitor against a month with no Sent log entry.
5. Replace test recipients with the stakeholder group. Confirm the shared mailbox
   display name, connection owner and backup flow co-owner.
6. Leave `PUBLISH_ENABLED=true`. Change **repository** variable
   `AUTOMATION_ENABLED` to `true`.
7. Leave both Power Automate flows turned on. Record the owners and first expected
   delivery date in the firm's automation register.

The next run starts on the fifth at 09:17 India time. It processes the previous
calendar month. The UTC schedule in the workflow is `47 3 5 * *`. This is a start
time, not an email-delivery deadline; GitHub scheduling may be delayed. The next-day
monitor catches a missing or failed monthly job even if GitHub never starts it.

## 9. Operate, revise and recover

**See what happened:** GitHub Actions provides build logs and 14-day artifacts;
SharePoint outbox proves publication; `DoTDeliveryLog` proves the delivery flow's
status. `Sent` means Outlook accepted the send, not that every recipient read it.
Assign GitHub Actions failure notifications to the technical owners in their
GitHub notification settings. Also enable Power Automate failure notifications.

**Late orders:** Run the affected month manually with publication enabled. Changed
source bytes/extracted content produce a new package key and separate email. Existing
versions remain intact. There is no automatic late-publication recheck in this first
deployment. The covering-letter date determines the month, and source discovery
currently searches the following 31 days as well as matching catalogue titles.
Orders published beyond that window may require investigation/manual backfill.

**Deliberate document reissue:** If formatting changes but substantive contents do
not, set revision to a new value such as `format-fix-1`. Otherwise keep `original`.
Changing revision deliberately creates a new release and can cause another email.
There is no automatic force-resend option for an existing package key.

**Failed upload:** Fix the connection/permission issue and rerun. Do not manually
create or copy an outbox JSON to bypass the upload checks.

**Uncertain email:** Follow the recovery instructions in POWER-AUTOMATE.md. Do not
delete the delivery log and retry before checking whether the original email left
the mailbox. Email sending and a SharePoint status update are separate operations;
this design does not claim transactional, exactly-once delivery.

**Stop new monthly builds:** Set `AUTOMATION_ENABLED=false`. This does not cancel
an already-running job or stop queued outbox delivery. For a full pause, also cancel
the active GitHub run and turn off the delivery flow. Keep the monitor on only if
you want missing-delivery alerts during the pause.

**Source outage/WAF:** The collector limits requests, but a GitHub-hosted IP can
still be blocked. Review the logs and retry later. If the host is persistently
blocked, IT must provide an approved runner/network; do not try to bypass source
access restrictions.

**Updating dependencies/code:** Review changes, run Tests, then run a historical
preview before merging/releasing. Actions use official major-version tags; IT may
pin reviewed action commit hashes according to its supply-chain policy. The pinned
Python dependency set is from the existing local installation; GitHub's first clean
Linux install and reference comparison are required before production sign-off.

## 10. Acceptance checklist

- [ ] Repository is private, firm-owned and on `main`.
- [ ] Microsoft environment allows only `main`; unattended runs do not wait for approval.
- [ ] Initial Tests and historical Linux preview pass.
- [ ] OCR works and outputs match the reviewed historical reference.
- [ ] Application can upload only within its selected SharePoint site.
- [ ] Stakeholders can open both files using their own accounts.
- [ ] Failed/partial builds do not create an outbox record.
- [ ] Identical reruns do not generate another email.
- [ ] Uncertain email delivery is held for investigation.
- [ ] Missing-delivery monitor is tested and has an owner.
- [ ] Production mailbox, recipients, retention and backup owners are recorded.
- [ ] `AUTOMATION_ENABLED` and `PUBLISH_ENABLED` are `true` only after the pilot.

## Official references

- [GitHub Desktop repository creation](https://docs.github.com/en/desktop/overview/creating-your-first-repository-using-github-desktop)
- [GitHub scheduled events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub federated identity with Microsoft](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-azure)
- [Microsoft client-credentials token exchange](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)
- [Selected SharePoint permissions](https://learn.microsoft.com/en-us/graph/permissions-selected-overview)
- [Granting access to a site](https://learn.microsoft.com/en-us/graph/api/site-post-permissions?view=graph-rest-1.0)
- [Uploading large files](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0)
- [SharePoint connector](https://learn.microsoft.com/en-us/connectors/sharepointonline/)
- [Outlook connector](https://learn.microsoft.com/en-us/connectors/office365/)
- [Power Automate licensing](https://learn.microsoft.com/en-us/power-platform/admin/power-automate-licensing/faqs)
