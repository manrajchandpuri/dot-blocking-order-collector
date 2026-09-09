<# Run once by the firm's Microsoft administrator. Requires PowerShell 7 and
Microsoft.Graph.Authentication. The operator must be able to register apps,
grant Microsoft Graph application permissions, and grant SharePoint site access.
This script does not create a flow, mailbox, tenant, Azure subscription or GitHub repo.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$TenantId,
    [Parameter(Mandatory)][string]$GitHubOwner,
    [Parameter(Mandatory)][string]$GitHubRepository,
    [Parameter(Mandatory)][string]$SharePointHost,
    [Parameter(Mandatory)][string]$SitePath,
    [Parameter(Mandatory)][string]$LibraryName,
    [string]$AppClientId = ''
)
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.Graph.Authentication
Connect-MgGraph -TenantId $TenantId -Scopes 'Application.ReadWrite.All','AppRoleAssignment.ReadWrite.All','Sites.FullControl.All' -NoWelcome
function Graph([string]$Method, [string]$Path, $Body = $null) {
    $argsMap = @{ Method=$Method; Uri="https://graph.microsoft.com/v1.0$Path"; OutputType='Hashtable' }
    if ($null -ne $Body) {
        $argsMap.Body = $Body | ConvertTo-Json -Depth 20
        $argsMap.ContentType = 'application/json'
    }
    Invoke-MgGraphRequest @argsMap
}
function AllPages([string]$Path) {
    $page = Graph GET $Path
    $items = @($page.value)
    while ($page.'@odata.nextLink') {
        $page = Invoke-MgGraphRequest -Method GET -Uri $page.'@odata.nextLink' -OutputType Hashtable
        $items += @($page.value)
    }
    return $items
}
try {
    if (-not $AppClientId) {
        $app = Graph POST '/applications' @{ displayName='DoT Blocking Order Collector'; signInAudience='AzureADMyOrg' }
        $AppClientId = $app.appId
        Write-Host "Created application. Save this client ID now for reruns: $AppClientId"
    } else {
        $apps = @(AllPages "/applications?`$filter=appId eq '$AppClientId'")
        if ($apps.Count -ne 1) { throw 'Could not find the specified application' }
        $app = $apps[0]
    }
    $principals = @(AllPages "/servicePrincipals?`$filter=appId eq '$AppClientId'")
    $sp = if ($principals.Count) { $principals[0] } else { Graph POST '/servicePrincipals' @{appId=$AppClientId} }
    $msgraph = @(AllPages "/servicePrincipals?`$filter=appId eq '00000003-0000-0000-c000-000000000000'")[0]
    $role = @($msgraph.appRoles | Where-Object { $_.value -eq 'Sites.Selected' -and $_.allowedMemberTypes -contains 'Application' })[0]
    if (-not $role) { throw 'Sites.Selected application role not found' }
    $assignments = @(AllPages "/servicePrincipals/$($sp.id)/appRoleAssignments")
    if (-not ($assignments | Where-Object { $_.resourceId -eq $msgraph.id -and $_.appRoleId -eq $role.id })) {
        $null = Graph POST "/servicePrincipals/$($sp.id)/appRoleAssignments" @{
            principalId=$sp.id; resourceId=$msgraph.id; appRoleId=$role.id
        }
    }
    $subject = "repo:${GitHubOwner}/${GitHubRepository}:environment:microsoft-production"
    $credentials = @(AllPages "/applications/$($app.id)/federatedIdentityCredentials")
    $matching = @($credentials | Where-Object { $_.name -eq 'github-microsoft-production' })
    if ($matching.Count) {
        if ($matching[0].subject -ne $subject -or $matching[0].issuer -ne 'https://token.actions.githubusercontent.com') {
            throw 'Existing federated credential targets another repository; investigate before changing it'
        }
    } else {
        $null = Graph POST "/applications/$($app.id)/federatedIdentityCredentials" @{
            name='github-microsoft-production'; issuer='https://token.actions.githubusercontent.com';
            subject=$subject; audiences=@('api://AzureADTokenExchange')
        }
    }
    $encodedSite = (($SitePath.Trim('/') -split '/') | ForEach-Object { [uri]::EscapeDataString($_) }) -join '/'
    $site = Graph GET "/sites/${SharePointHost}:/${encodedSite}"
    $permissions = @(AllPages "/sites/$($site.id)/permissions")
    $siteGrant = @($permissions | Where-Object {
        $identities = @($_.grantedToIdentitiesV2) + @($_.grantedToIdentities)
        @($identities | Where-Object { $_.application.id -eq $AppClientId }).Count -gt 0
    })
    if (-not $siteGrant.Count) {
        $null = Graph POST "/sites/$($site.id)/permissions" @{
            roles=@('write'); grantedToIdentities=@(@{application=@{id=$AppClientId; displayName='DoT Blocking Order Collector'}})
        }
    } elseif (-not ($siteGrant | Where-Object { $_.roles -contains 'write' -or $_.roles -contains 'fullcontrol' })) {
        throw 'An existing site grant lacks write access. Review it before proceeding.'
    }
    $drives = @(AllPages "/sites/$($site.id)/drives")
    $drive = @($drives | Where-Object { $_.name -eq $LibraryName })
    if ($drive.Count -ne 1) { throw "Library not found or ambiguous: $LibraryName" }
    $drive = $drive[0]
    $children = @(AllPages "/drives/$($drive.id)/root/children")
    $root = @($children | Where-Object { $_.name -eq 'DoT Collector' })
    if (-not $root.Count) {
        $root = @(Graph POST "/drives/$($drive.id)/root/children" @{
            name='DoT Collector'; folder=@{}; '@microsoft.graph.conflictBehavior'='fail'
        })
    }
    if (-not $root[0].ContainsKey('folder')) { throw 'DoT Collector exists but is not a folder' }
    Write-Host 'Copy these NON-SECRET values into the GitHub environment variables:'
    [ordered]@{
        AZURE_TENANT_ID=$TenantId; AZURE_CLIENT_ID=$AppClientId;
        SHAREPOINT_DRIVE_ID=$drive.id; SHAREPOINT_ROOT_FOLDER_ID=$root[0].id;
        PUBLISH_ENABLED='false'
    } | ConvertTo-Json
    Write-Host "SharePoint folder: $($root[0].webUrl)"
} finally {
    Disconnect-MgGraph | Out-Null
}
