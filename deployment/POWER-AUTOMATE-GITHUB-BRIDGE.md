# No-admin delivery: GitHub Actions to Power Automate to SharePoint

Use this route when an Entra administrator has not approved a direct GitHub
Actions-to-SharePoint application. It uses the operator's GitHub connection and
their SharePoint connection in Power Automate. It does not use Microsoft Graph,
PowerShell, an Entra application, or GitHub-stored Microsoft credentials.

## Security and operating model

The validated ZIP, DOCX and QA files are committed to the private
`automation-output` branch in `delivery/YYYY-MM/`, with the newest validated
package also copied to `delivery/current/`. `ready.json` identifies the period,
package key and exact filenames.

Power Automate is triggered by the branch commit, reads those files through the
GitHub connector, writes them to SharePoint, records the package key in a
SharePoint list, then sends an email containing SharePoint links. The SharePoint
connection uses the operator's existing site permission. Do not turn this on
unless storing the generated reports in the private GitHub repository is allowed
by the firm's information-security policy.

## One-time GitHub setup

1. In the repository, select the branch picker and create a branch named
   `automation-output` from `main`.
2. Go to **Settings -> Actions -> General -> Workflow permissions**. Select
   **Read and write permissions**, then save.
3. Go to **Settings -> Secrets and variables -> Actions -> Variables** and add:

   | Name | Value |
   | --- | --- |
   | `GITHUB_DELIVERY_ENABLED` | `false` during testing, then `true` |

4. Keep `AUTOMATION_ENABLED=false` until the end-to-end test has succeeded.
   Keep `PUBLISH_ENABLED=false`; this no-admin route does not use direct Graph
   publishing.

## Test the GitHub release

1. In Actions, run **Monthly DoT collector** manually.
2. Enter the test reporting month, select **Publish**, and leave the revision as
   `original`.
3. With `GITHUB_DELIVERY_ENABLED=false`, confirm that the collection is successful
   and the summary says that release was skipped.
4. Change `GITHUB_DELIVERY_ENABLED` to `true`.
5. Run the same validated test again. Confirm that `automation-output` now contains:

   ```text
   delivery/YYYY-MM/ready.json
   delivery/YYYY-MM/<the ZIP named in ready.json>
   delivery/YYYY-MM/<the DOCX named in ready.json>
   delivery/YYYY-MM/<the QA CSV named in ready.json>
   delivery/YYYY-MM/<the QA HTML named in ready.json>
   delivery/current/ready.json
   ```

## Power Automate flow

Create an **Automated cloud flow**, not a scheduled or pull-request flow.

1. Choose the GitHub trigger **When a commit is pushed**. Enter:
   - Repository owner: `manrajchandpuri`
   - Repository: `dot-blocking-order-collector`
   - Branch: `automation-output`
2. Add a GitHub file-content action for this fixed path:

   ```text
   delivery/current/ready.json
   ```

   Parse the JSON. The flow must stop unless `status` is `Ready`; obtain the
   period and filenames from the parsed JSON.
3. Create a SharePoint list called **DoTDeliveryLog**, with a unique single-line
   text `Title` column. Use `package_key` as Title. Before delivering, query the
   list for that key. If present, terminate successfully. This prevents duplicate
   delivery on a repeated commit.
4. Create the folder `Documents/DoT Collector/Incoming/<period>` in the test site.
5. For each entry under `files` in `ready.json`, retrieve the GitHub file content
   from `delivery/current/<name>` and use SharePoint **Create file** to upload it
   into the period folder.
6. Create the delivery-log row only after all files have uploaded. Then send a
   test email to yourself containing the SharePoint folder link. Do not attach the
   ZIP to email.

Only after this test is successful: change `AUTOMATION_ENABLED` to `true`. The
workflow's cron runs every fifth at 03:47 UTC (09:17 India time) and defaults to
the previous calendar month.
