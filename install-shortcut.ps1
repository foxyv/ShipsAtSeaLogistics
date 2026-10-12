# Creates desktop and Start Menu shortcuts that start the planner without a console window.
#
# Both shortcuts carry the same AppUserModelID the app sets on itself at startup. Windows uses the
# Start Menu one to work out what a taskbar pin should look like and launch, so a pinned planner keeps
# its own icon while closed instead of falling back to pythonw.exe's.
$appId = "SasLogistics.Planner"  # must match SetCurrentProcessExplicitAppUserModelID in app\saslogi\__main__.py
$app = Join-Path $PSScriptRoot "app"
$icon = Join-Path $app "icon.ico"

$pyw = (Get-Command pyw.exe -ErrorAction Stop).Source
Push-Location $app
try {
    # The icon is drawn in code; write it out once so the shortcuts have a file to point at.
    py -c "import sys; from PySide6.QtGui import QGuiApplication; from saslogi.icon import make_icon; app = QGuiApplication(['-platform', 'offscreen']); sys.exit(0 if make_icon().pixmap(256, 256).save(sys.argv[1], 'ICO') else 1)" $icon
    if ($LASTEXITCODE -ne 0) { throw "Could not write $icon" }
} finally { Pop-Location }

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class ShortcutAppId
{
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    private class ShellLink { }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    private struct PropertyKey { public Guid FormatId; public uint PropertyId; }

    [StructLayout(LayoutKind.Explicit, Size = 24)]
    private struct PropVariant { [FieldOffset(0)] public ushort Type; [FieldOffset(8)] public IntPtr Pointer; }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPropertyStore
    {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, out PropVariant value);
        void SetValue(ref PropertyKey key, ref PropVariant value);
        void Commit();
    }

    private static PropertyKey AppUserModelId = new PropertyKey { FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), PropertyId = 5 };
    private const ushort VT_LPWSTR = 31;

    public static void Set(string shortcutPath, string appId)
    {
        object link = new ShellLink();
        ((IPersistFile)link).Load(shortcutPath, 2);  // STGM_READWRITE
        PropVariant value = new PropVariant { Type = VT_LPWSTR, Pointer = Marshal.StringToCoTaskMemUni(appId) };
        try
        {
            IPropertyStore store = (IPropertyStore)link;
            store.SetValue(ref AppUserModelId, ref value);
            store.Commit();
            ((IPersistFile)link).Save(shortcutPath, true);
        }
        finally { Marshal.FreeCoTaskMem(value.Pointer); }
    }

    public static string Get(string shortcutPath)
    {
        object link = new ShellLink();
        ((IPersistFile)link).Load(shortcutPath, 0);
        PropVariant value;
        ((IPropertyStore)link).GetValue(ref AppUserModelId, out value);
        return value.Type == VT_LPWSTR ? Marshal.PtrToStringUni(value.Pointer) : null;
    }
}
'@

foreach ($folder in "Desktop", "Programs") {
    $link = Join-Path ([Environment]::GetFolderPath($folder)) "Ships At Sea Logistics.lnk"
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
    $shortcut.TargetPath = $pyw
    $shortcut.Arguments = "-m saslogi"
    $shortcut.WorkingDirectory = $app
    $shortcut.IconLocation = "$icon,0"
    $shortcut.Description = "Ships At Sea logistics planner"
    $shortcut.Save()
    [ShortcutAppId]::Set($link, $appId)
    "Created $link (app id: $([ShortcutAppId]::Get($link)))"
}
