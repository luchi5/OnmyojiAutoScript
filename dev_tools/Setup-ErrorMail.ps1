param([switch]$ValidateOnly)
# This helper has no credential-read, SMTP, game-control or settings-enable code.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class OasErrorMailCredentialWriter {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr handle, int command);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr handle);
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct Credential {
        public uint Flags; public uint Type;
        public string TargetName; public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize; public IntPtr CredentialBlob;
        public uint Persist; public uint AttributeCount; public IntPtr Attributes;
        public string TargetAlias; public string UserName;
    }
    [DllImport("Advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool Write(ref Credential credential, uint flags);
}
'@
$address = 'your-account@gmail.com'
$target = 'OAS/ErrorMail/Gmail/' + $address
$projectRoot = Split-Path -Parent $PSScriptRoot
$statusPath = Join-Path $projectRoot 'config\error_mail\setup_status.json'
$script:credentialSaved = $false
function Set-SetupStatus([string]$phase) {
    # Safe progress only. No credential or form contents are serialized.
    @{status=$phase; updated_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress |
        Set-Content -LiteralPath $statusPath -Encoding UTF8
}
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object System.Windows.Forms.Form
$form.Text = 'OAS Gmail Error Mail Setup'
$form.ClientSize = New-Object System.Drawing.Size(580, 310)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.TopMost = $true
$label = New-Object System.Windows.Forms.Label
$label.Location = New-Object System.Drawing.Point(20, 18)
$label.Size = New-Object System.Drawing.Size(540, 100)
$label.Text = "Sender and recipient: $address`r`nStore: Windows Credential Manager (current Windows user)`r`nPaste a Google app password below. Input stays hidden.`r`nSaving does not enable alerts, send a test, or change the game."
$form.Controls.Add($label)
$passwordLabel = New-Object System.Windows.Forms.Label
$passwordLabel.Location = New-Object System.Drawing.Point(20, 125)
$passwordLabel.Size = New-Object System.Drawing.Size(530, 22)
$passwordLabel.Text = 'Google app password (16 letters; displayed spaces are accepted):'
$form.Controls.Add($passwordLabel)
$passwordBox = New-Object System.Windows.Forms.TextBox
$passwordBox.Location = New-Object System.Drawing.Point(20, 152)
$passwordBox.Size = New-Object System.Drawing.Size(530, 28)
$passwordBox.UseSystemPasswordChar = $true
$passwordBox.MaxLength = 64
$form.Controls.Add($passwordBox)
$save = New-Object System.Windows.Forms.Button
$save.Location = New-Object System.Drawing.Point(20, 197)
$save.Size = New-Object System.Drawing.Size(180, 34)
$save.Text = 'Save credential'
$form.Controls.Add($save)
$close = New-Object System.Windows.Forms.Button
$close.Location = New-Object System.Drawing.Point(215, 197)
$close.Size = New-Object System.Drawing.Size(120, 34)
$close.Text = 'Close'
$close.Add_Click({$form.Close()})
$form.Controls.Add($close)
$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Location = New-Object System.Drawing.Point(20, 247)
$statusLabel.Size = New-Object System.Drawing.Size(540, 48)
$statusLabel.Text = 'Waiting for local input. Never paste the app password into chat.'
$form.Controls.Add($statusLabel)
$form.AcceptButton = $save
$form.Add_Shown({
    # The helper host console is hidden; explicitly reveal only this input form.
    [void][OasErrorMailCredentialWriter]::ShowWindow($form.Handle, 5)
    [void][OasErrorMailCredentialWriter]::SetForegroundWindow($form.Handle)
})
$save.Add_Click({
    $secret = New-Object System.Security.SecureString
    $characters = $null
    $nativeSecret = [IntPtr]::Zero
    $save.Enabled = $false
    try {
        $characters = $passwordBox.Text.ToCharArray()
        $passwordBox.Clear()
        foreach($character in $characters) {
            if ([char]::IsWhiteSpace($character)) { continue }
            $letter = [int]$character
            if (!(($letter -ge 65 -and $letter -le 90) -or ($letter -ge 97 -and $letter -le 122))) {
                throw 'format'
            }
            $secret.AppendChar($character)
        }
        if ($secret.Length -ne 16) { throw 'format' }
        $secret.MakeReadOnly()
        $answer = [System.Windows.Forms.MessageBox]::Show($form,
            "Save or replace this app password for $address in your current Windows user's Credential Manager?`r`nTarget: $target`r`nNo email will be sent by this window.",
            'Confirm credential storage', [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question, [System.Windows.Forms.MessageBoxDefaultButton]::Button2)
        if ($answer -ne [System.Windows.Forms.DialogResult]::Yes) {
            $statusLabel.Text = 'Not saved. Enter the app password again only if you want to save it.'
            return
        }
        $nativeSecret = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        $credential = New-Object OasErrorMailCredentialWriter+Credential
        $credential.Type = 1
        $credential.TargetName = $target
        $credential.UserName = $address
        $credential.CredentialBlobSize = $secret.Length * 2
        $credential.CredentialBlob = $nativeSecret
        $credential.Persist = 2
        $credential.Comment = 'OAS Gmail error mail only; saved by local user confirmation'
        if (![OasErrorMailCredentialWriter]::Write([ref]$credential, 0)) { throw 'write' }
        $script:credentialSaved = $true
        Set-SetupStatus 'saved'
        $passwordBox.Enabled = $false
        $statusLabel.Text = 'Saved. No email was sent. You may close this window and return to chat.'
    } catch {
        # Do not display exception objects or UI input. CredWrite may already have
        # succeeded if the safe status-file write failed: report that distinctly.
        if ($script:credentialSaved) {
            $passwordBox.Enabled = $false
            $statusLabel.Text = 'Credential saved; progress-file update failed. Return to chat; do not save again.'
        } else {
            $statusLabel.Text = 'Not saved. Check the 16-letter app password or Windows permission, then try manually.'
        }
    } finally {
        if ($nativeSecret -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($nativeSecret) }
        if ($null -ne $characters) { [Array]::Clear($characters, 0, $characters.Length) }
        $character = $null
        $secret.Dispose()
        if (!$script:credentialSaved) { $save.Enabled = $true }
    }
})
$form.Add_FormClosing({
    $passwordBox.Clear()
    if (!$script:credentialSaved) { Set-SetupStatus 'closed_without_save' }
})
if ($ValidateOnly) {
    if (!$passwordBox.UseSystemPasswordChar -or $form.Text -ne 'OAS Gmail Error Mail Setup') { throw 'UI validation failed' }
    if ($form.ClientSize.Width -lt 580 -or $save.Text -ne 'Save credential') { throw 'UI validation failed' }
    $form.Dispose()
    Write-Output 'Hidden-input UI and native credential-writer declaration validated; no credential API called.'
    return
}
Set-SetupStatus 'waiting_for_user'
[void]$form.ShowDialog()
$form.Dispose()
