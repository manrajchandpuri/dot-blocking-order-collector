# Your next steps — existing private repository

Your repository has already been created:

**https://github.com/manrajchandpuri/dot-blocking-order-collector**

It is private, on branch `main`, and currently contains GitHub's initial README.
The prepared application has **not** been uploaded. No Microsoft tenant settings,
flows or credentials have been configured, and no monthly schedule is enabled.

## 1. Upload the complete package with GitHub Desktop

1. Download/open GitHub Desktop from https://desktop.github.com/ and sign in as
   `manrajchandpuri`.
2. Choose **File → Clone repository → URL**.
3. Paste `https://github.com/manrajchandpuri/dot-blocking-order-collector`.
4. Choose a new local folder outside the original working project, then **Clone**.
5. In Finder, extract `dist/dot-blocking-order-collector-github.zip` from the
   original DoT Blocking Order Collector project.
6. Press **Command–Shift–.** to show hidden files.
7. Copy **everything inside the extracted package** into the cloned repository
   folder. Replace its initial README when prompted. Do not copy an outer wrapper
   folder; `src/` and `.github/` must be directly inside the clone. Leave the clone's
   existing `.git` folder alone; the package does not contain one.
8. Confirm the clone contains these top-level items:

```text
.github/
  workflows/monthly-collector.yml
  workflows/tests.yml
.gitignore
deployment/
scripts/
src/
tests/
README.md
requirements.txt
requirements-automation.txt
serve.py
Run DoT Collector.command
```

9. Return to GitHub Desktop. Review the Changes list. The upload must not contain
   `.venv`, `workspace`, downloaded order PDFs, output documents, passwords or tokens.
10. Enter commit summary **Upload collector and monthly Microsoft deployment**.
11. Click **Commit to main**, then **Push origin**.
12. Open the repository on GitHub. Confirm the source folders and both workflow
    files are visible. Open **Actions → Tests** and check the first run.

Do not click “Publish repository” or create a second repository: this one already
exists. Do not upload the source ZIP as a single file. GitHub needs the extracted
workflow files to recognise and run the automation.

Keep visibility **Private**. GitHub Actions does not require a public repository.
You do not need to grant the Codex GitHub connector access to perform this manual
upload yourself. Only grant IT access if needed, using named collaborators or the
firm's organisation. Moving the repository to the firm later changes its OIDC
identity: update the Microsoft federated credential's owner/repository at that time.

## 2. Configure GitHub and Microsoft

Continue at **section 3** of [DEPLOYMENT-GUIDE.md](DEPLOYMENT-GUIDE.md).
Sections 1–2 are reference instructions for a future firm-owned repository.

Create environment `microsoft-production`, restrict it to `main`, and add repository
variable `AUTOMATION_ENABLED=false`. If GitHub says private environments require
an upgrade, ask IT about using the firm's supported plan; do not make the repository
public to work around it. No paid upgrade has been purchased or authorised here.

Give IT `setup-microsoft.ps1` and section 4 of the guide. For **this repository**, use:

```powershell
./deployment/setup-microsoft.ps1 `
  -TenantId 'YOUR-FIRM-TENANT-GUID' `
  -GitHubOwner 'manrajchandpuri' `
  -GitHubRepository 'dot-blocking-order-collector' `
  -SharePointHost 'YOUR-FIRM.sharepoint.com' `
  -SitePath '/sites/YOUR-SITE' `
  -LibraryName 'YOUR-LIBRARY-DISPLAY-NAME'
```

The exact federated subject for this repository is:

```text
repo:manrajchandpuri/dot-blocking-order-collector:environment:microsoft-production
```

IT supplies the Microsoft tenant/site/library values. Do not invent them or put
your Microsoft password into GitHub. The script prints the non-secret environment
variables to copy; the full guide explains their locations and permissions.

## 3. Preview, publish a pilot, build delivery, then enable scheduling

Follow guide sections **5–8** in order:

1. Run a historical preview with publication unchecked.
2. Inspect and compare its ZIP, Word document and quality reports.
3. Enable `PUBLISH_ENABLED=true` and publish a pilot to SharePoint.
4. Follow [POWER-AUTOMATE.md](POWER-AUTOMATE.md) to create the delivery log, delivery
   flow and independent monitor, using test recipients first.
5. Confirm links, duplicate suppression, error handling and missing-delivery alerts.
6. Set the production stakeholder group and leave both flows on.
7. Finally set repository variable `AUTOMATION_ENABLED=true`.

Default production start: fifth of each month at **09:17 India time**, collecting
the previous calendar month. Email follows successful generation and publication.

## What has been verified locally

- 98 tests passed; three historical/network-dependent checks skipped.
- Python modules compile and workflow YAML parses.
- Completion records match the supplied Power Automate JSON schema.
- The source ZIP's contents and per-file hashes match its upload manifest.
- All pinned runtime dependency versions exist on the official Python registry.

The Microsoft administrator script and Power Automate flows have not been run in
your tenant. The Linux install, live DoT collection from GitHub and email delivery
still require the pilot. Local verification does not replace those deployment checks.
