param([ValidateSet('probe','context','text','screenshot')][string]$Action = 'probe')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Drawing
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class PilotWindow {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out Rect r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
}
'@
    function Get-Context {
        $handle = [PilotWindow]::GetForegroundWindow()
        if ($handle -eq [IntPtr]::Zero) { throw 'no_focused_window' }
        [uint32]$ownerId = 0
        [void][PilotWindow]::GetWindowThreadProcessId($handle, [ref]$ownerId)
        $caption = New-Object System.Text.StringBuilder 2048
        [void][PilotWindow]::GetWindowText($handle, $caption, $caption.Capacity)
        $owner = Get-Process -Id $ownerId
        $secure = $false
        try { $secure = [System.Windows.Automation.AutomationElement]::FocusedElement.Current.IsPassword } catch { }
        return @{ app_id=$owner.ProcessName; process_id=[int]$ownerId; window_id=$handle.ToInt64().ToString(); title=$caption.ToString(); record_ref=''; secure_context=$secure; evidence_level='native' }
    }
    function Assert-Same($expected, $current) {
        foreach ($key in @('window_id','process_id','title','record_ref')) {
            if ([string]$expected.$key -cne [string]$current.$key) { throw 'focus_changed' }
        }
    }
    if ($Action -eq 'probe') {
        $result = @{ platform='Windows'; uia_available=$true; permission_requested=$false; screen_capture_attempted=$false; interactive_session=[Environment]::UserInteractive }
    } else {
        $current = Get-Context
        if ($Action -eq 'context') { $result = $current }
        else {
            $expected = [Console]::In.ReadToEnd() | ConvertFrom-Json
            Assert-Same $expected $current
            if ($current.secure_context) { throw 'protected_surface' }
            $handle = [IntPtr]::new([long]$current.window_id)
            if ($Action -eq 'text') {
                $root = [System.Windows.Automation.AutomationElement]::FromHandle($handle)
                $pending = New-Object 'System.Collections.Generic.Stack[System.Windows.Automation.AutomationElement]'
                $pending.Push($root)
                $text = New-Object System.Text.StringBuilder
                $visited = 0
                while ($pending.Count -gt 0 -and $visited -lt 256 -and $text.Length -lt 16000) {
                    $element = $pending.Pop()
                    $visited++
                    if ($element.Current.IsPassword -or $element.Current.IsOffscreen) { continue }
                    $pattern = $null
                    if ($element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) {
                        [void]$text.AppendLine($pattern.DocumentRange.GetText(16000 - $text.Length))
                    } elseif ($element.Current.Name) {
                        [void]$text.AppendLine($element.Current.Name.Substring(0, [Math]::Min($element.Current.Name.Length, 16000 - $text.Length)))
                    }
                    $child = [System.Windows.Automation.TreeWalker]::ControlViewWalker.GetFirstChild($element)
                    $children = 0
                    while ($null -ne $child -and $children -lt 256) {
                        $pending.Push($child)
                        $child = [System.Windows.Automation.TreeWalker]::ControlViewWalker.GetNextSibling($child)
                        $children++
                    }
                }
                Assert-Same $expected (Get-Context)
                $result = $current
                $result.text = $text.ToString().Substring(0, [Math]::Min($text.Length, 16000))
            } else {
                $rect = New-Object PilotWindow+Rect
                if (-not [PilotWindow]::GetWindowRect($handle, [ref]$rect)) { throw 'screenshot_unavailable' }
                $width = $rect.Right - $rect.Left
                $height = $rect.Bottom - $rect.Top
                if ($width -lt 2 -or $height -lt 2 -or $width -gt 4096 -or $height -gt 4096) { throw 'window_too_large' }
                $bitmap = New-Object System.Drawing.Bitmap $width, $height
                $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
                $memory = New-Object System.IO.MemoryStream
                try {
                    $dc = $graphics.GetHdc()
                    try { $success = [PilotWindow]::PrintWindow($handle, $dc, 2) } finally { $graphics.ReleaseHdc($dc) }
                    if (-not $success) { throw 'screenshot_unavailable' }
                    Assert-Same $expected (Get-Context)
                    $first = $bitmap.GetPixel(0, 0).ToArgb()
                    $uniform = $true
                    foreach ($x in @(0.2,0.5,0.8)) { foreach ($y in @(0.2,0.5,0.8)) {
                        if ($bitmap.GetPixel([int](($width-1)*$x), [int](($height-1)*$y)).ToArgb() -ne $first) { $uniform=$false }
                    } }
                    if ($uniform) { throw 'screenshot_unusable' }
                    $maxEdge = 1280
                    $saved = $bitmap
                    $scaledGraphics = $null
                    if ($width -gt $maxEdge -or $height -gt $maxEdge) {
                        $scale = $maxEdge / [Math]::Max($width, $height)
                        $targetW = [int][Math]::Max(1, $width * $scale)
                        $targetH = [int][Math]::Max(1, $height * $scale)
                        $saved = New-Object System.Drawing.Bitmap $targetW, $targetH
                        $scaledGraphics = [System.Drawing.Graphics]::FromImage($saved)
                        $scaledGraphics.DrawImage($bitmap, 0, 0, $targetW, $targetH)
                    }
                    $saved.Save($memory, [System.Drawing.Imaging.ImageFormat]::Png)
                    if ($scaledGraphics) { $scaledGraphics.Dispose() }
                    if ($saved -ne $bitmap) { $saved.Dispose() }
                    $result = @{ image_base64=[Convert]::ToBase64String($memory.ToArray()); media_type='image/png'; single_window=$true; max_edge=$maxEdge }
                } finally { $memory.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }
            }
        }
    }
    $result | ConvertTo-Json -Compress -Depth 6
} catch {
    $known = @('permission_denied','no_focused_window','focus_changed','protected_surface','screenshot_unavailable','screenshot_unusable','window_too_large')
    $code = if ($known -contains $_.Exception.Message) { $_.Exception.Message } else { 'native_helper_failed' }
    @{ error=$code } | ConvertTo-Json -Compress
    exit 1
}
