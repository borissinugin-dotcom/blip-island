"""Persistent PowerShell WinRT worker for GSMTC media control, Windows OCR (RU+EN), and notifications."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import threading
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

_PS_WORKER_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Media.Ocr, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType=WindowsRuntime]
$null = [Windows.Devices.Radios.RadioAccessStatus, Windows.System.Devices, ContentType=WindowsRuntime]
$null = [Windows.UI.Notifications.Management.UserNotificationListener, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.UI.Notifications.NotificationKinds, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.UI.Notifications.UserNotification, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.UI.Notifications.KnownNotificationBindings, Windows.UI.Notifications, ContentType=WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await-WinRT($op, $resultType) {
    $m = $asTaskGeneric.MakeGenericMethod($resultType)
    $t = $m.Invoke($null, @($op))
    if ($t.Wait(650)) { return $t.Result }
    return $null
}

$asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
})[0]

function Await-Action($op) {
    $t = $asTaskAction.Invoke($null, @($op))
    $t.Wait()
}

$script:gsmtcMgr = Await-WinRT ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$script:lastArtKey = ""
$script:lastArtB64 = ""

function Read-ThumbnailB64($thumbRef, $artKey) {
    if ($null -eq $thumbRef) { return "" }
    if ($artKey -eq $script:lastArtKey -and $script:lastArtB64 -ne "") {
        return $script:lastArtB64
    }
    try {
        $stream = Await-WinRT ($thumbRef.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
        if ($null -eq $stream) { return "" }
        $size = [uint32]$stream.Size
        if ($size -le 0 -or $size -gt 6291456) {
            $stream.Dispose()
            return ""
        }
        $netStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream)
        $ms = New-Object System.IO.MemoryStream
        $netStream.CopyTo($ms)
        $bytes = $ms.ToArray()
        $ms.Dispose()
        $netStream.Dispose()
        $stream.Dispose()
        if ($bytes.Length -le 0) { return "" }
        $b64 = [Convert]::ToBase64String($bytes)
        $script:lastArtKey = $artKey
        $script:lastArtB64 = $b64
        return $b64
    } catch {
        return ""
    }
}

function Get-MediaState($preferredAppId, $skipThumb) {
    $freshMgr = Await-WinRT ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
    if ($null -ne $freshMgr) {
        $script:gsmtcMgr = $freshMgr
    }
    if ($null -eq $script:gsmtcMgr) {
        return @{ ok = $false; sessions = @(); active = $null }
    }
    $sessions = $script:gsmtcMgr.GetSessions()
    $curSession = $script:gsmtcMgr.GetCurrentSession()
    $curId = if ($curSession) { [string]$curSession.SourceAppUserModelId } else { "" }
    $list = @()
    $parsed = @()
    foreach ($s in $sessions) {
        try {
            $appId = [string]$s.SourceAppUserModelId
            $pb = $s.GetPlaybackInfo()
            $status = [string]$pb.PlaybackStatus
            if ($status -eq 'Closed') { continue }
            $tl = $s.GetTimelineProperties()
            $props = Await-WinRT ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
            $title = if ($props) { [string]$props.Title } else { "" }
            $artist = if ($props) { [string]$props.Artist } else { "" }
            $album = if ($props) { [string]$props.AlbumTitle } else { "" }
            $dur = if ($tl) { [double]$tl.EndTime.TotalSeconds } else { 0.0 }
            $pos = if ($tl) { [double]$tl.Position.TotalSeconds } else { 0.0 }
            if ($tl -and $status -eq 'Playing' -and $tl.LastUpdatedTime.Year -gt 1970) {
                $delta = ([DateTimeOffset]::UtcNow - $tl.LastUpdatedTime).TotalSeconds
                if ($delta -gt 0 -and $delta -lt 3600) { $pos += $delta }
            }
            if ($dur -gt 0 -and $pos -gt $dur) { $pos = $dur }
            $updTicks = if ($tl) { [int64]$tl.LastUpdatedTime.UtcTicks } else { [int64]0 }
            $item = @{
                app_id = $appId
                title = $title
                artist = $artist
                album = $album
                status = $status
                position = [Math]::Round($pos, 2)
                duration = [Math]::Round($dur, 2)
                thumbnail_b64 = ""
            }
            $list += $item
            $parsed += @{ item = $item; thumb = if ($props) { $props.Thumbnail } else { $null }; upd = $updTicks }
        } catch {}
    }

    # Always order newest-updated sessions first so swiped / newly started tracks win over stale sessions
    $parsed = @($parsed | Sort-Object { $_.upd } -Descending)

    $chosen = $null
    if ($preferredAppId) {
        $chosen = $parsed | Where-Object { $_.item.app_id -eq $preferredAppId } | Select-Object -First 1
        if ($null -eq $chosen) {
            $chosen = $parsed | Where-Object { $_.item.app_id.ToLower().Contains($preferredAppId.ToLower()) } | Select-Object -First 1
        }
    }
    if ($null -eq $chosen) {
        $chosen = $parsed | Where-Object { $_.item.status -eq 'Playing' -and ($_.item.title -or $_.item.artist) } | Select-Object -First 1
    }
    if ($null -eq $chosen -and $curId) {
        $chosen = $parsed | Where-Object { $_.item.app_id -eq $curId -and ($_.item.title -or $_.item.artist) } | Select-Object -First 1
    }
    if ($null -eq $chosen) {
        $chosen = $parsed | Where-Object { $_.item.title -or $_.item.artist } | Select-Object -First 1
    }

    $activeData = $null
    if ($null -ne $chosen) {
        $it = $chosen.item
        $artKey = "$($it.app_id)|$($it.title)|$($it.artist)|$($it.album)"
        if ($skipThumb -and $artKey -ne $script:lastArtKey) {
            $it.thumbnail_b64 = ""
        } else {
            $it.thumbnail_b64 = Read-ThumbnailB64 $chosen.thumb $artKey
        }
        $activeData = $it
    }
    return @{ ok = $true; sessions = $list; active = $activeData }
}

function Invoke-MediaCmd($appId, $action, $value) {
    if ($null -eq $script:gsmtcMgr) { return @{ ok = $false } }
    $target = $null
    if ($appId) {
        foreach ($s in $script:gsmtcMgr.GetSessions()) {
            if ($s.SourceAppUserModelId -eq $appId) { $target = $s; break }
        }
    }
    if ($null -eq $target) { $target = $script:gsmtcMgr.GetCurrentSession() }
    if ($null -eq $target) { return @{ ok = $false; error = "no_session" } }
    $ok = $false
    switch ($action) {
        "play_pause" { $ok = Await-WinRT ($target.TryTogglePlayPauseAsync()) ([bool]) }
        "next" { $ok = Await-WinRT ($target.TrySkipNextAsync()) ([bool]) }
        "prev" { $ok = Await-WinRT ($target.TrySkipPreviousAsync()) ([bool]) }
        "seek" {
            $ticks = [long]([double]$value * 10000000.0)
            $ok = Await-WinRT ($target.TryChangePlaybackPositionAsync($ticks)) ([bool])
        }
    }
    return @{ ok = [bool]$ok }
}

function Invoke-DualOcr($imagePath) {
    if (-not (Test-Path $imagePath)) { return @{ ok = $false; text = ""; error = "file_not_found" } }
    try {
        $file = Await-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imagePath)) ([Windows.Storage.StorageFile])
        $stream = Await-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $sbmp = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $stream.Dispose()

        $linesOut = @()
        $ruLang = New-Object Windows.Globalization.Language("ru")
        $enLang = New-Object Windows.Globalization.Language("en-US")
        $ruEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($ruLang)
        $enEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($enLang)

        $ruRes = if ($ruEngine) { Await-WinRT ($ruEngine.RecognizeAsync($sbmp)) ([Windows.Media.Ocr.OcrResult]) } else { $null }
        $enRes = if ($enEngine) { Await-WinRT ($enEngine.RecognizeAsync($sbmp)) ([Windows.Media.Ocr.OcrResult]) } else { $null }
        $sbmp.Dispose()

        $ruLines = if ($ruRes) { @($ruRes.Lines) } else { @() }
        $enLines = if ($enRes) { @($enRes.Lines) } else { @() }
        $maxLen = [Math]::Max($ruLines.Count, $enLines.Count)
        for ($i = 0; $i -lt $maxLen; $i++) {
            $rText = if ($i -lt $ruLines.Count) { [string]$ruLines[$i].Text } else { "" }
            $eText = if ($i -lt $enLines.Count) { [string]$enLines[$i].Text } else { "" }
            if ($rText -match '[\u0400-\u04FF]') {
                $linesOut += $rText
            } elseif ($eText) {
                $linesOut += $eText
            } elseif ($rText) {
                $linesOut += $rText
            }
        }
        return @{ ok = $true; text = ($linesOut -join "`n") }
    } catch {
        return @{ ok = $false; text = ""; error = [string]$_.Exception.Message }
    }
}

function Get-RadioStates() {
    try {
        $radios = Await-WinRT ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
        $wifiOn = $null
        $btOn = $null
        $hasWifi = $false
        $hasBt = $false
        foreach ($r in $radios) {
            $k = [string]$r.Kind
            $isOn = ([string]$r.State -eq 'On')
            if ($k -eq 'WiFi') {
                $hasWifi = $true
                if ($null -eq $wifiOn -or $isOn) { $wifiOn = $isOn }
            } elseif ($k -eq 'Bluetooth') {
                $hasBt = $true
                if ($null -eq $btOn -or $isOn) { $btOn = $isOn }
            }
        }
        return @{ ok = $true; wifi = $wifiOn; bt = $btOn; wifi_present = $hasWifi; bt_present = $hasBt }
    } catch {
        return @{ ok = $false; wifi = $null; bt = $null; error = [string]$_.Exception.Message }
    }
}

function Invoke-RadioToggle($kind) {
    try {
        $null = Await-WinRT ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
        $radios = Await-WinRT ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
        $target = if ($kind -eq 'wifi') { 'WiFi' } else { 'Bluetooth' }
        $toggled = $false
        $newState = $false
        foreach ($r in $radios) {
            if ([string]$r.Kind -eq $target) {
                $next = if ([string]$r.State -eq 'On') {
                    [Windows.Devices.Radios.RadioState]::Off
                } else {
                    [Windows.Devices.Radios.RadioState]::On
                }
                $st = Await-WinRT ($r.SetStateAsync($next)) ([Windows.Devices.Radios.RadioAccessStatus])
                if ([string]$st -eq 'Allowed') {
                    $toggled = $true
                    $newState = ([string]$next -eq 'On')
                }
            }
        }
        $all = Get-RadioStates
        $all.ok = $toggled
        $all.toggled = $toggled
        $all.state = $newState
        return $all
    } catch {
        return @{ ok = $false; toggled = $false; error = [string]$_.Exception.Message }
    }
}

$script:notifListener = $null
$script:seenNotifIds = $null

function Get-SystemNotifications() {
    try {
        if ($null -eq $script:notifListener) {
            $script:notifListener = [Windows.UI.Notifications.Management.UserNotificationListener]::Current
        }
        $list = Await-WinRT ($script:notifListener.GetNotificationsAsync([Windows.UI.Notifications.NotificationKinds]::Toast)) ([System.Collections.Generic.IReadOnlyList[Windows.UI.Notifications.UserNotification]])
        if ($null -eq $list) {
            return @{ ok = $true; notifications = @() }
        }
        if ($null -eq $script:seenNotifIds) {
            $script:seenNotifIds = New-Object 'System.Collections.Generic.HashSet[uint32]'
            foreach ($n in $list) {
                $null = $script:seenNotifIds.Add([uint32]$n.Id)
            }
            return @{ ok = $true; notifications = @() }
        }
        $out = @()
        foreach ($n in $list) {
            $nid = [uint32]$n.Id
            if ($script:seenNotifIds.Add($nid)) {
                $app = ""
                $appId = ""
                try { $app = [string]$n.AppInfo.DisplayInfo.DisplayName } catch {}
                try { $appId = [string]$n.AppInfo.AppUserModelId } catch {}
                if (-not $app) { $app = $appId }
                $binding = $n.Notification.Visual.GetBinding([Windows.UI.Notifications.KnownNotificationBindings]::ToastGeneric)
                $texts = @()
                if ($binding) {
                    foreach ($te in $binding.GetTextElements()) {
                        $t = ([string]$te.Text).Trim()
                        if ($t -ne "") { $texts += $t }
                    }
                }
                if ($texts.Count -gt 0) {
                    $t0 = [string]$texts[0]
                    $tRest = if ($texts.Count -gt 1) { ($texts | Select-Object -Skip 1) -join " — " } else { "" }
                    $out += @{
                        id = [int]$nid
                        app = $app
                        app_id = $appId
                        title = $t0
                        body = $tRest
                    }
                }
            }
        }
        return @{ ok = $true; notifications = $out }
    } catch {
        return @{ ok = $false; notifications = @(); error = [string]$_.Exception.Message }
    }
}

Write-Output '{"ready":true}'
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    $line = $line.Trim()
    if ($line -eq "") { continue }
    try {
        $req = $line | ConvertFrom-Json
        $resp = switch ($req.cmd) {
            "ping" { @{ ok = $true } }
            "media_state" { Get-MediaState ([string]$req.app_id) ([bool]$req.skip_thumb) }
            "media_cmd" { Invoke-MediaCmd ([string]$req.app_id) ([string]$req.action) $req.value }
            "ocr" { Invoke-DualOcr ([string]$req.path) }
            "radio_state" { Get-RadioStates }
            "radio_toggle" { Invoke-RadioToggle ([string]$req.kind) }
            "notifications" { Get-SystemNotifications }
            "quit" { break }
            default { @{ ok = $false; error = "unknown_cmd" } }
        }
        $json = $resp | ConvertTo-Json -Compress -Depth 6
        [Console]::Out.WriteLine($json)
        [Console]::Out.Flush()
    } catch {
        $errJson = (@{ ok = $false; error = [string]$_.Exception.Message } | ConvertTo-Json -Compress)
        [Console]::Out.WriteLine($errJson)
        [Console]::Out.Flush()
    }
}
"""


class WinRTWorker:
    """Thread-safe persistent PowerShell WinRT worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None

    def _ensure_ps_script(self) -> Path:
        digest = hashlib.sha256(_PS_WORKER_SCRIPT.encode("utf-8")).hexdigest()[:12]
        path = Path(tempfile.gettempdir()) / f"borh_winrt_worker_{digest}.ps1"
        if not path.exists():
            path.write_text(_PS_WORKER_SCRIPT, encoding="utf-8-sig")
        return path

    def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        ps_path = self._ensure_ps_script()
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        assert self._proc.stdin is not None and self._proc.stdout is not None
        ready_line = self._proc.stdout.readline().strip()
        if "ready" not in ready_line:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"WinRTWorker failed to initialize: {ready_line} {err}")

    def request(self, payload: dict) -> dict:
        with self._lock:
            for attempt in range(2):
                try:
                    self._ensure_started()
                    assert self._proc is not None
                    if self._proc.pid != getattr(self, "_nice_applied_pid", 0):
                        from .win32_api import set_process_below_normal

                        set_process_below_normal(self._proc.pid)
                        self._nice_applied_pid = self._proc.pid
                    assert self._proc.stdin is not None and self._proc.stdout is not None
                    self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    self._proc.stdin.flush()
                    line = self._proc.stdout.readline().strip()
                    if not line:
                        raise RuntimeError("Empty response from WinRTWorker")
                    return json.loads(line)
                except Exception:
                    self.stop()
                    if attempt == 1:
                        return {"ok": False, "error": "winrt_worker_failed"}
        return {"ok": False}

    def get_media_state(self, preferred_app_id: str = "", *, skip_thumb: bool = False) -> dict:
        return self.request({"cmd": "media_state", "app_id": preferred_app_id, "skip_thumb": bool(skip_thumb)})

    def send_media_command(self, action: str, app_id: str = "", value: float = 0.0) -> bool:
        resp = self.request({"cmd": "media_cmd", "app_id": app_id, "action": action, "value": value})
        return bool(resp.get("ok"))

    def recognize_text(self, image_path: str) -> str:
        resp = self.request({"cmd": "ocr", "path": image_path})
        return str(resp.get("text", "") or "")

    def get_radio_states(self) -> dict:
        return self.request({"cmd": "radio_state"})

    def toggle_radio(self, kind: str) -> dict:
        return self.request({"cmd": "radio_toggle", "kind": kind})

    def get_notifications(self) -> list[dict]:
        resp = self.request({"cmd": "notifications"})
        items = resp.get("notifications")
        if isinstance(items, dict):
            return [items]
        if isinstance(items, list):
            return items
        return []

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write('{"cmd":"quit"}\n')
                proc.stdin.flush()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream:
                    try:
                        stream.close()
                    except Exception:
                        pass
