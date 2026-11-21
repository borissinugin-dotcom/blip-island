// Native Windows bridge for CoreAudio (devices, volume, WASAPI loopback spectrum),
// WMI monitor brightness, and Windows Media Foundation H.264 MP4 recording.
// Compiled on demand via built-in C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Management;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace BorhWinNative
{
    internal static class Program
    {
        [DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        [STAThread]
        private static int Main(string[] args)
        {
            try { SetProcessDPIAware(); } catch { }
            Console.OutputEncoding = Encoding.UTF8;
            Console.InputEncoding = Encoding.UTF8;
            if (args.Length == 0)
            {
                Console.Error.WriteLine("Usage: audio-devices | volume-get | volume-set | mute-set | loopback-stream | brightness-get | brightness-set | mp4-record");
                return 1;
            }

            try
            {
                string cmd = args[0].ToLowerInvariant();
                switch (cmd)
                {
                    case "audio-devices":
                        Console.WriteLine(AudioBridge.GetDevicesJson());
                        return 0;
                    case "volume-get":
                        {
                            string flow = args.Length > 1 ? args[1] : "render";
                            string devId = args.Length > 2 ? args[2] : "default";
                            Console.WriteLine(AudioBridge.GetVolumeJson(flow, devId));
                            return 0;
                        }
                    case "volume-set":
                        {
                            string flow = args[1];
                            string devId = args[2];
                            float level = float.Parse(args[3], CultureInfo.InvariantCulture) / 100f;
                            AudioBridge.SetVolume(flow, devId, Math.Max(0f, Math.Min(1f, level)));
                            return 0;
                        }
                    case "mute-set":
                        {
                            string flow = args[1];
                            string devId = args[2];
                            bool mute = args[3] == "1" || args[3].Equals("true", StringComparison.OrdinalIgnoreCase);
                            AudioBridge.SetMute(flow, devId, mute);
                            return 0;
                        }
                    case "app-volume-get":
                        {
                            string procHint = args.Length > 1 ? args[1] : "spotify";
                            string devId = args.Length > 2 ? args[2] : "default";
                            Console.WriteLine(AudioBridge.GetAppVolumeJson(procHint, devId));
                            return 0;
                        }
                    case "app-volume-set":
                        {
                            string procHint = args[1];
                            string devId = args[2];
                            float level = float.Parse(args[3], CultureInfo.InvariantCulture) / 100f;
                            bool ok = AudioBridge.SetAppVolume(procHint, devId, Math.Max(0f, Math.Min(1f, level)));
                            Console.WriteLine(ok ? "{\"ok\":true}" : "{\"ok\":false}");
                            return 0;
                        }
                    case "app-sessions":
                        {
                            string devId = args.Length > 1 ? args[1] : "default";
                            Console.WriteLine(AudioBridge.ListAppSessionsJson(devId));
                            return 0;
                        }
                    case "app-mute-set":
                        {
                            string procHint = args[1];
                            string devId = args[2];
                            bool mute = args[3] == "1" || args[3].Equals("true", StringComparison.OrdinalIgnoreCase);
                            bool ok = AudioBridge.SetAppMute(procHint, devId, mute);
                            Console.WriteLine(ok ? "{\"ok\":true}" : "{\"ok\":false}");
                            return 0;
                        }
                    case "app-profile-apply":
                        {
                            string devId = args.Length > 1 ? args[1] : "default";
                            string spec = args.Length > 2 ? args[2] : "";
                            int applied = AudioBridge.ApplyAppProfileBatch(devId, spec);
                            Console.WriteLine(string.Format(CultureInfo.InvariantCulture, "{{\"applied\":{0}}}", applied));
                            return 0;
                        }
                    case "loopback-stream":
                        {
                            string devId = args.Length > 1 ? args[1] : "default";
                            AudioBridge.StreamLoopbackSpectrum(devId);
                            return 0;
                        }
                    case "brightness-get":
                        Console.WriteLine(BrightnessBridge.GetBrightness());
                        return 0;
                    case "brightness-set":
                        BrightnessBridge.SetBrightness(int.Parse(args[1], CultureInfo.InvariantCulture));
                        return 0;
                    case "mp4-record":
                        {
                            int x = int.Parse(args[1], CultureInfo.InvariantCulture);
                            int y = int.Parse(args[2], CultureInfo.InvariantCulture);
                            int w = int.Parse(args[3], CultureInfo.InvariantCulture);
                            int h = int.Parse(args[4], CultureInfo.InvariantCulture);
                            int fps = int.Parse(args[5], CultureInfo.InvariantCulture);
                            string outPath = args[6];
                            string audioMode = args.Length > 7 ? args[7] : "none";
                            long targetHwnd = args.Length > 8 ? long.Parse(args[8], CultureInfo.InvariantCulture) : 0L;
                            return Mp4Recorder.RecordRegion(x, y, w, h, fps, outPath, audioMode, new IntPtr(targetHwnd));
                        }
                    case "mp4-pipe":
                        {
                            int w = int.Parse(args[1], CultureInfo.InvariantCulture);
                            int h = int.Parse(args[2], CultureInfo.InvariantCulture);
                            int fps = int.Parse(args[3], CultureInfo.InvariantCulture);
                            string outPath = args[4];
                            return Mp4Recorder.RecordFromStdinPipe(w, h, fps, outPath);
                        }
                    default:
                        Console.Error.WriteLine("Unknown command: " + cmd);
                        return 2;
                }
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(ex.ToString());
                return 3;
            }
        }
    }

    internal static class JsonEsc
    {
        public static string Esc(string s)
        {
            if (s == null) return "";
            var sb = new StringBuilder(s.Length + 8);
            foreach (char c in s)
            {
                if (c == '\\') sb.Append("\\\\");
                else if (c == '"') sb.Append("\\\"");
                else if (c == '\n') sb.Append("\\n");
                else if (c == '\r') sb.Append("\\r");
                else if (c == '\t') sb.Append("\\t");
                else if (c < 32) sb.AppendFormat("\\u{0:X4}", (int)c);
                else sb.Append(c);
            }
            return sb.ToString();
        }
    }

    #region CoreAudio COM Interop
    internal enum EDataFlow { eRender = 0, eCapture = 1, eAll = 2 }
    internal enum ERole { eConsole = 0, eMultimedia = 1, eCommunications = 2 }

    [StructLayout(LayoutKind.Sequential)]
    internal struct PROPERTYKEY
    {
        public Guid fmtid;
        public int pid;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct PROPVARIANT
    {
        public ushort vt;
        public ushort wReserved1;
        public ushort wReserved2;
        public ushort wReserved3;
        public IntPtr p;
        public int p2;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct WAVEFORMATEX
    {
        public ushort wFormatTag;
        public ushort nChannels;
        public uint nSamplesPerSec;
        public uint nAvgBytesPerSec;
        public ushort nBlockAlign;
        public ushort wBitsPerSample;
        public ushort cbSize;
    }

    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    internal class MMDeviceEnumeratorCom { }

    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IMMDeviceEnumerator
    {
        [PreserveSig] int EnumAudioEndpoints(EDataFlow dataFlow, uint dwStateMask, out IMMDeviceCollection ppDevices);
        [PreserveSig] int GetDefaultAudioEndpoint(EDataFlow dataFlow, ERole role, out IMMDevice ppEndpoint);
        [PreserveSig] int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string pwstrId, out IMMDevice ppDevice);
    }

    [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IMMDeviceCollection
    {
        [PreserveSig] int GetCount(out uint pcDevices);
        [PreserveSig] int Item(uint nDevice, out IMMDevice ppDevice);
    }

    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IMMDevice
    {
        [PreserveSig] int Activate(ref Guid iid, uint dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
        [PreserveSig] int OpenPropertyStore(uint stgmAccess, out IPropertyStore ppProperties);
        [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string ppstrId);
        [PreserveSig] int GetState(out uint pdwState);
    }

    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IPropertyStore
    {
        [PreserveSig] int GetCount(out uint cProps);
        [PreserveSig] int GetAt(uint iProp, out PROPERTYKEY pkey);
        [PreserveSig] int GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
    }

    [ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAudioEndpointVolume
    {
        [PreserveSig] int RegisterControlChangeNotify(IntPtr pNotify);
        [PreserveSig] int UnregisterControlChangeNotify(IntPtr pNotify);
        [PreserveSig] int GetChannelCount(out uint pnChannelCount);
        [PreserveSig] int SetMasterVolumeLevel(float fLevelDB, ref Guid pguidEventContext);
        [PreserveSig] int SetMasterVolumeLevelScalar(float fLevel, ref Guid pguidEventContext);
        [PreserveSig] int GetMasterVolumeLevel(out float pfLevelDB);
        [PreserveSig] int GetMasterVolumeLevelScalar(out float pfLevel);
        [PreserveSig] int SetChannelVolumeLevel(uint nChannel, float fLevelDB, ref Guid pguidEventContext);
        [PreserveSig] int SetChannelVolumeLevelScalar(uint nChannel, float fLevel, ref Guid pguidEventContext);
        [PreserveSig] int GetChannelVolumeLevel(uint nChannel, out float pfLevelDB);
        [PreserveSig] int GetChannelVolumeLevelScalar(uint nChannel, out float pfLevel);
        [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, ref Guid pguidEventContext);
        [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool pbMute);
    }

    [ComImport, Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAudioMeterInformation
    {
        [PreserveSig] int GetPeakValue(out float pfPeak);
    }

    [ComImport, Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAudioClient
    {
        [PreserveSig] int Initialize(int ShareMode, uint StreamFlags, long hnsBufferDuration, long hnsPeriodicity, IntPtr pFormat, ref Guid AudioSessionGuid);
        [PreserveSig] int GetBufferSize(out uint pNumBufferFrames);
        [PreserveSig] int GetStreamLatency(out long phnsLatency);
        [PreserveSig] int GetCurrentPadding(out uint pNumPaddingFrames);
        [PreserveSig] int IsFormatSupported(int ShareMode, IntPtr pFormat, out IntPtr ppClosestMatch);
        [PreserveSig] int GetMixFormat(out IntPtr ppDeviceFormat);
        [PreserveSig] int GetDevicePeriod(out long phnsDefaultDevicePeriod, out long phnsMinimumDevicePeriod);
        [PreserveSig] int Start();
        [PreserveSig] int Stop();
        [PreserveSig] int Reset();
        [PreserveSig] int SetEventHandle(IntPtr eventHandle);
        [PreserveSig] int GetService(ref Guid riid, [MarshalAs(UnmanagedType.IUnknown)] out object ppv);
    }

    [ComImport, Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAudioCaptureClient
    {
        [PreserveSig] int GetBuffer(out IntPtr ppData, out uint pNumFramesToRead, out uint pdwFlags, out ulong pu64DevicePosition, out ulong pu64QPCPosition);
        [PreserveSig] int ReleaseBuffer(uint NumFramesRead);
        [PreserveSig] int GetNextPacketSize(out uint pNumFramesInNextPacket);
    }

    [ComImport, Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface ISimpleAudioVolume
    {
        [PreserveSig] int SetMasterVolume(float fLevel, ref Guid EventContext);
        [PreserveSig] int GetMasterVolume(out float pfLevel);
        [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, ref Guid EventContext);
        [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool pbMute);
    }

    [ComImport, Guid("F4B1A599-7266-4319-A8CA-E70ACB11E8CD"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAudioSessionControl
    {
        [PreserveSig] int GetState(out int pRetVal);
        [PreserveSig] int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
        [PreserveSig] int SetDisplayName([MarshalAs(UnmanagedType.LPWStr)] string Value, ref Guid EventContext);
        [PreserveSig] int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
        [PreserveSig] int SetIconPath([MarshalAs(UnmanagedType.LPWStr)] string Value, ref Guid EventContext);
        [PreserveSig] int GetGroupingParam(out Guid pRetVal);
        [PreserveSig] int SetGroupingParam(ref Guid Override, ref Guid EventContext);
        [PreserveSig] int RegisterAudioSessionNotification(IntPtr NewNotifications);
        [PreserveSig] int UnregisterAudioSessionNotification(IntPtr NewNotifications);
    }

    [ComImport, Guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAudioSessionControl2
    {
        [PreserveSig] int GetState(out int pRetVal);
        [PreserveSig] int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
        [PreserveSig] int SetDisplayName([MarshalAs(UnmanagedType.LPWStr)] string Value, ref Guid EventContext);
        [PreserveSig] int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
        [PreserveSig] int SetIconPath([MarshalAs(UnmanagedType.LPWStr)] string Value, ref Guid EventContext);
        [PreserveSig] int GetGroupingParam(out Guid pRetVal);
        [PreserveSig] int SetGroupingParam(ref Guid Override, ref Guid EventContext);
        [PreserveSig] int RegisterAudioSessionNotification(IntPtr NewNotifications);
        [PreserveSig] int UnregisterAudioSessionNotification(IntPtr NewNotifications);
        [PreserveSig] int GetSessionIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
        [PreserveSig] int GetSessionInstanceIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
        [PreserveSig] int GetProcessId(out uint pRetVal);
        [PreserveSig] int IsSystemSoundsSession();
        [PreserveSig] int SetDuckingPreference([MarshalAs(UnmanagedType.Bool)] bool optOut);
    }

    [ComImport, Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAudioSessionEnumerator
    {
        [PreserveSig] int GetCount(out int SessionCount);
        [PreserveSig] int GetSession(int SessionCount, out IAudioSessionControl Session);
    }

    [ComImport, Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAudioSessionManager2
    {
        [PreserveSig] int GetAudioSessionControl(ref Guid AudioSessionGuid, uint StreamFlags, out IAudioSessionControl SessionControl);
        [PreserveSig] int GetSimpleAudioVolume(ref Guid AudioSessionGuid, uint StreamFlags, out ISimpleAudioVolume AudioVolume);
        [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator SessionEnum);
    }

    internal static class AudioBridge
    {
        private static readonly Guid IID_IAudioEndpointVolume = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
        private static readonly Guid IID_IAudioMeterInformation = new Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064");
        private static readonly Guid IID_IAudioClient = new Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2");
        private static readonly Guid IID_IAudioCaptureClient = new Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317");
        private static readonly Guid IID_IAudioSessionManager2 = new Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F");
        private static readonly PROPERTYKEY PKEY_Device_FriendlyName = new PROPERTYKEY
        {
            fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"),
            pid = 14
        };

        [DllImport("ole32.dll")]
        private static extern int PropVariantClear(ref PROPVARIANT pvar);

        private static IMMDevice ResolveDevice(IMMDeviceEnumerator enumerator, string flowStr, string devId)
        {
            EDataFlow flow = flowStr.Equals("capture", StringComparison.OrdinalIgnoreCase)
                ? EDataFlow.eCapture
                : EDataFlow.eRender;
            IMMDevice dev;
            if (string.IsNullOrEmpty(devId) || devId.Equals("default", StringComparison.OrdinalIgnoreCase))
            {
                Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(flow, ERole.eMultimedia, out dev));
                return dev;
            }
            if (enumerator.GetDevice(devId, out dev) == 0 && dev != null)
            {
                return dev;
            }
            Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(flow, ERole.eMultimedia, out dev));
            return dev;
        }

        private static string GetFriendlyName(IMMDevice dev)
        {
            IPropertyStore props;
            if (dev.OpenPropertyStore(0, out props) != 0 || props == null) return "Audio Device";
            try
            {
                PROPERTYKEY key = PKEY_Device_FriendlyName;
                PROPVARIANT pv;
                if (props.GetValue(ref key, out pv) == 0)
                {
                    try
                    {
                        if (pv.vt == 31 && pv.p != IntPtr.Zero) // VT_LPWSTR
                        {
                            return Marshal.PtrToStringUni(pv.p) ?? "Audio Device";
                        }
                    }
                    finally
                    {
                        PropVariantClear(ref pv);
                    }
                }
            }
            finally
            {
                Marshal.ReleaseComObject(props);
            }
            return "Audio Device";
        }

        public static string GetDevicesJson()
        {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
            var sb = new StringBuilder();
            sb.Append("{");
            sb.Append("\"render\":");
            AppendFlowDevices(enumerator, EDataFlow.eRender, sb);
            sb.Append(",\"capture\":");
            AppendFlowDevices(enumerator, EDataFlow.eCapture, sb);
            sb.Append("}");
            return sb.ToString();
        }

        private static void AppendFlowDevices(IMMDeviceEnumerator enumerator, EDataFlow flow, StringBuilder sb)
        {
            string defaultId = "";
            IMMDevice defDev;
            if (enumerator.GetDefaultAudioEndpoint(flow, ERole.eMultimedia, out defDev) == 0 && defDev != null)
            {
                defDev.GetId(out defaultId);
                Marshal.ReleaseComObject(defDev);
            }

            IMMDeviceCollection col;
            Marshal.ThrowExceptionForHR(enumerator.EnumAudioEndpoints(flow, 1 /* DEVICE_STATE_ACTIVE */, out col));
            uint count;
            col.GetCount(out count);
            sb.Append("[");
            for (uint i = 0; i < count; i++)
            {
                IMMDevice dev;
                col.Item(i, out dev);
                string id;
                dev.GetId(out id);
                string name = GetFriendlyName(dev);
                bool isDef = string.Equals(id, defaultId, StringComparison.OrdinalIgnoreCase);
                if (i > 0) sb.Append(",");
                sb.AppendFormat(
                    "{{\"id\":\"{0}\",\"name\":\"{1}\",\"is_default\":{2}}}",
                    JsonEsc.Esc(id),
                    JsonEsc.Esc(name),
                    isDef ? "true" : "false"
                );
                Marshal.ReleaseComObject(dev);
            }
            sb.Append("]");
            Marshal.ReleaseComObject(col);
        }

        public static string GetVolumeJson(string flow, string devId)
        {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
            IMMDevice dev = ResolveDevice(enumerator, flow, devId);
            try
            {
                object epObj;
                Guid iid = IID_IAudioEndpointVolume;
                Marshal.ThrowExceptionForHR(dev.Activate(ref iid, 23, IntPtr.Zero, out epObj));
                var ep = (IAudioEndpointVolume)epObj;
                float level;
                bool mute;
                ep.GetMasterVolumeLevelScalar(out level);
                ep.GetMute(out mute);

                float peak = 0f;
                object meterObj;
                Guid meterIid = IID_IAudioMeterInformation;
                if (dev.Activate(ref meterIid, 23, IntPtr.Zero, out meterObj) == 0 && meterObj != null)
                {
                    ((IAudioMeterInformation)meterObj).GetPeakValue(out peak);
                    Marshal.ReleaseComObject(meterObj);
                }

                Marshal.ReleaseComObject(ep);
                return string.Format(
                    CultureInfo.InvariantCulture,
                    "{{\"volume\":{0},\"muted\":{1},\"peak\":{2}}}",
                    (int)Math.Round(level * 100f),
                    mute ? "true" : "false",
                    peak.ToString("0.0000", CultureInfo.InvariantCulture)
                );
            }
            finally
            {
                Marshal.ReleaseComObject(dev);
            }
        }

        public static void SetVolume(string flow, string devId, float level)
        {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
            IMMDevice dev = ResolveDevice(enumerator, flow, devId);
            try
            {
                object epObj;
                Guid iid = IID_IAudioEndpointVolume;
                Marshal.ThrowExceptionForHR(dev.Activate(ref iid, 23, IntPtr.Zero, out epObj));
                var ep = (IAudioEndpointVolume)epObj;
                Guid ctx = Guid.Empty;
                Marshal.ThrowExceptionForHR(ep.SetMasterVolumeLevelScalar(level, ref ctx));
                Marshal.ReleaseComObject(ep);
            }
            finally
            {
                Marshal.ReleaseComObject(dev);
            }
        }

        public static void SetMute(string flow, string devId, bool mute)
        {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
            IMMDevice dev = ResolveDevice(enumerator, flow, devId);
            try
            {
                object epObj;
                Guid iid = IID_IAudioEndpointVolume;
                Marshal.ThrowExceptionForHR(dev.Activate(ref iid, 23, IntPtr.Zero, out epObj));
                var ep = (IAudioEndpointVolume)epObj;
                Guid ctx = Guid.Empty;
                Marshal.ThrowExceptionForHR(ep.SetMute(mute, ref ctx));
                Marshal.ReleaseComObject(ep);
            }
            finally
            {
                Marshal.ReleaseComObject(dev);
            }
        }

        private static bool MatchSessionProcess(IAudioSessionControl ctrl, string hint)
        {
            var ctrl2 = ctrl as IAudioSessionControl2;
            if (ctrl2 == null) return false;
            string clean = (hint ?? "").Replace(".exe", "").Trim();
            if (clean.Length == 0) return false;
            if (clean.Equals("__system__", StringComparison.OrdinalIgnoreCase))
            {
                return ctrl2.IsSystemSoundsSession() == 0;
            }
            uint pid = 0;
            if (ctrl2.GetProcessId(out pid) != 0) pid = 0;
            if (clean.StartsWith("pid:", StringComparison.OrdinalIgnoreCase))
            {
                uint targetPid = 0;
                return uint.TryParse(clean.Substring(4), out targetPid) && pid == targetPid;
            }
            if (pid == 0) return false;
            try
            {
                string pname = Process.GetProcessById((int)pid).ProcessName ?? "";
                if (pname.IndexOf(clean, StringComparison.OrdinalIgnoreCase) >= 0) return true;
                string ident = "";
                ctrl2.GetSessionIdentifier(out ident);
                return !string.IsNullOrEmpty(ident) && ident.IndexOf(clean, StringComparison.OrdinalIgnoreCase) >= 0;
            }
            catch
            {
                return false;
            }
        }

        private static void ForEachRenderSession(string devId, Func<IAudioSessionControl, bool> visitor)
        {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
            IMMDevice dev = ResolveDevice(enumerator, "render", devId);
            try
            {
                object mgrObj;
                Guid iidMgr = IID_IAudioSessionManager2;
                Marshal.ThrowExceptionForHR(dev.Activate(ref iidMgr, 23, IntPtr.Zero, out mgrObj));
                var mgr = (IAudioSessionManager2)mgrObj;
                IAudioSessionEnumerator sesEnum;
                if (mgr.GetSessionEnumerator(out sesEnum) == 0 && sesEnum != null)
                {
                    int count = 0;
                    sesEnum.GetCount(out count);
                    for (int i = 0; i < count; i++)
                    {
                        IAudioSessionControl ctrl;
                        if (sesEnum.GetSession(i, out ctrl) == 0 && ctrl != null)
                        {
                            bool keepGoing = true;
                            try
                            {
                                keepGoing = visitor(ctrl);
                            }
                            finally
                            {
                                Marshal.ReleaseComObject(ctrl);
                            }
                            if (!keepGoing) break;
                        }
                    }
                    Marshal.ReleaseComObject(sesEnum);
                }
                Marshal.ReleaseComObject(mgr);
            }
            finally
            {
                Marshal.ReleaseComObject(dev);
            }
        }

        public static string ListAppSessionsJson(string devId)
        {
            var sb = new StringBuilder();
            sb.Append('[');
            bool first = true;
            ForEachRenderSession(devId, delegate(IAudioSessionControl ctrl)
            {
                var ctrl2 = ctrl as IAudioSessionControl2;
                var simp = ctrl as ISimpleAudioVolume;
                if (ctrl2 == null || simp == null) return true;

                int state = 0;
                ctrl.GetState(out state);
                if (state == 2) return true; // Skip expired sessions

                bool isSys = ctrl2.IsSystemSoundsSession() == 0;
                uint pid = 0;
                if (ctrl2.GetProcessId(out pid) != 0) pid = 0;

                string pname = isSys ? "__system__" : "";
                string exePath = "";
                if (!isSys && pid != 0)
                {
                    try
                    {
                        using (var p = Process.GetProcessById((int)pid))
                        {
                            pname = p.ProcessName ?? "";
                            try { exePath = p.MainModule != null ? (p.MainModule.FileName ?? "") : ""; }
                            catch { exePath = ""; }
                        }
                    }
                    catch
                    {
                        return true;
                    }
                }
                if (string.IsNullOrEmpty(pname)) return true;

                string disp = "";
                try { ctrl.GetDisplayName(out disp); } catch { disp = ""; }
                if (string.IsNullOrWhiteSpace(disp) || disp.StartsWith("@", StringComparison.Ordinal))
                {
                    disp = isSys ? "System Sounds" : pname;
                }

                float lvl = 1f;
                bool mut = false;
                simp.GetMasterVolume(out lvl);
                simp.GetMute(out mut);

                if (!first) sb.Append(',');
                first = false;
                sb.AppendFormat(
                    CultureInfo.InvariantCulture,
                    "{{\"pid\":{0},\"process\":\"{1}\",\"title\":\"{2}\",\"exe\":\"{3}\",\"volume\":{4},\"muted\":{5},\"active\":{6},\"system\":{7}}}",
                    pid,
                    JsonEsc.Esc(pname),
                    JsonEsc.Esc(disp),
                    JsonEsc.Esc(exePath),
                    (int)Math.Round(lvl * 100f),
                    mut ? "true" : "false",
                    state == 1 ? "true" : "false",
                    isSys ? "true" : "false"
                );
                return true;
            });
            sb.Append(']');
            return sb.ToString();
        }

        public static string GetAppVolumeJson(string procHint, string devId)
        {
            string result = "{\"found\":false,\"volume\":100,\"muted\":false}";
            ForEachRenderSession(devId, delegate(IAudioSessionControl ctrl)
            {
                if (MatchSessionProcess(ctrl, procHint))
                {
                    var simp = ctrl as ISimpleAudioVolume;
                    if (simp != null)
                    {
                        float lvl = 1f;
                        bool mut = false;
                        simp.GetMasterVolume(out lvl);
                        simp.GetMute(out mut);
                        result = string.Format(
                            CultureInfo.InvariantCulture,
                            "{{\"found\":true,\"volume\":{0},\"muted\":{1}}}",
                            (int)Math.Round(lvl * 100f),
                            mut ? "true" : "false"
                        );
                        return false;
                    }
                }
                return true;
            });
            return result;
        }

        public static bool SetAppVolume(string procHint, string devId, float level)
        {
            bool changed = false;
            Guid ctx = Guid.Empty;
            ForEachRenderSession(devId, delegate(IAudioSessionControl ctrl)
            {
                if (MatchSessionProcess(ctrl, procHint))
                {
                    var simp = ctrl as ISimpleAudioVolume;
                    if (simp != null)
                    {
                        simp.SetMasterVolume(level, ref ctx);
                        changed = true;
                    }
                }
                return true;
            });
            return changed;
        }

        public static bool SetAppMute(string procHint, string devId, bool mute)
        {
            bool changed = false;
            Guid ctx = Guid.Empty;
            ForEachRenderSession(devId, delegate(IAudioSessionControl ctrl)
            {
                if (MatchSessionProcess(ctrl, procHint))
                {
                    var simp = ctrl as ISimpleAudioVolume;
                    if (simp != null)
                    {
                        simp.SetMute(mute, ref ctx);
                        changed = true;
                    }
                }
                return true;
            });
            return changed;
        }

        public static int ApplyAppProfileBatch(string devId, string spec)
        {
            if (string.IsNullOrWhiteSpace(spec)) return 0;
            var hints = new List<string>();
            var vols = new List<float>();
            var mutes = new List<bool>();
            string[] parts = spec.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries);
            foreach (string rawPart in parts)
            {
                int eq = rawPart.IndexOf('=');
                if (eq <= 0) continue;
                string hint = rawPart.Substring(0, eq).Trim();
                string val = rawPart.Substring(eq + 1).Trim();
                string[] vm = val.Split(':');
                float lvl = 1f;
                int pct;
                if (vm.Length >= 1 && int.TryParse(vm[0], NumberStyles.Integer, CultureInfo.InvariantCulture, out pct))
                {
                    lvl = Math.Max(0f, Math.Min(1f, pct / 100f));
                }
                bool mut = vm.Length >= 2 && (vm[1] == "1" || vm[1].Equals("true", StringComparison.OrdinalIgnoreCase));
                hints.Add(hint);
                vols.Add(lvl);
                mutes.Add(mut);
            }
            if (hints.Count == 0) return 0;

            int applied = 0;
            Guid ctx = Guid.Empty;
            ForEachRenderSession(devId, delegate(IAudioSessionControl ctrl)
            {
                var simp = ctrl as ISimpleAudioVolume;
                if (simp == null) return true;
                for (int k = 0; k < hints.Count; k++)
                {
                    if (MatchSessionProcess(ctrl, hints[k]))
                    {
                        simp.SetMasterVolume(vols[k], ref ctx);
                        simp.SetMute(mutes[k], ref ctx);
                        applied++;
                        break;
                    }
                }
                return true;
            });
            return applied;
        }

        internal static bool TryOpenWasapiCapture(
            bool loopback,
            out IAudioClient client,
            out IAudioCaptureClient cap,
            out WAVEFORMATEX fmt)
        {
            client = null;
            cap = null;
            fmt = default(WAVEFORMATEX);
            try
            {
                var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
                IMMDevice dev;
                int hr = enumerator.GetDefaultAudioEndpoint(
                    loopback ? EDataFlow.eRender : EDataFlow.eCapture,
                    ERole.eMultimedia,
                    out dev);
                if (hr != 0 || dev == null) return false;
                object clientObj;
                Guid iidClient = IID_IAudioClient;
                hr = dev.Activate(ref iidClient, 23, IntPtr.Zero, out clientObj);
                if (hr != 0 || clientObj == null) return false;
                client = (IAudioClient)clientObj;

                IntPtr pFmt;
                hr = client.GetMixFormat(out pFmt);
                if (hr != 0 || pFmt == IntPtr.Zero) return false;
                fmt = (WAVEFORMATEX)Marshal.PtrToStructure(pFmt, typeof(WAVEFORMATEX));

                uint flags = loopback ? 0x00020000u : 0u;
                Guid session = Guid.Empty;
                hr = client.Initialize(0, flags, 1000000, 0, pFmt, ref session);
                if (hr != 0) return false;

                object capObj;
                Guid iidCap = IID_IAudioCaptureClient;
                hr = client.GetService(ref iidCap, out capObj);
                if (hr != 0 || capObj == null) return false;
                cap = (IAudioCaptureClient)capObj;
                return client.Start() == 0;
            }
            catch
            {
                return false;
            }
        }

        public static void StreamLoopbackSpectrum(string devId)
        {
            try { Process.GetCurrentProcess().PriorityClass = ProcessPriorityClass.BelowNormal; } catch { }
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
            IMMDevice dev = ResolveDevice(enumerator, "render", devId);
            object clientObj;
            Guid iidClient = IID_IAudioClient;
            Marshal.ThrowExceptionForHR(dev.Activate(ref iidClient, 23, IntPtr.Zero, out clientObj));
            var client = (IAudioClient)clientObj;

            IntPtr pFmt;
            Marshal.ThrowExceptionForHR(client.GetMixFormat(out pFmt));
            WAVEFORMATEX fmt = (WAVEFORMATEX)Marshal.PtrToStructure(pFmt, typeof(WAVEFORMATEX));
            const uint AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000;
            Guid session = Guid.Empty;
            // 100ms buffer = 1_000_000 in 100ns units
            Marshal.ThrowExceptionForHR(client.Initialize(0, AUDCLNT_STREAMFLAGS_LOOPBACK, 1000000, 0, pFmt, ref session));

            object capObj;
            Guid iidCap = IID_IAudioCaptureClient;
            Marshal.ThrowExceptionForHR(client.GetService(ref iidCap, out capObj));
            var cap = (IAudioCaptureClient)capObj;

            Marshal.ThrowExceptionForHR(client.Start());

            // 512-sample mono ring buffer for lightweight real-time DFT into 20 log-spaced bands
            const int N = 512;
            float[] ring = new float[N];
            int ringPos = 0;
            int channels = Math.Max(1, (int)fmt.nChannels);
            int bytesPerSample = Math.Max(2, fmt.wBitsPerSample / 8);
            bool isFloat = fmt.wFormatTag == 3 || fmt.wFormatTag == 0xFFFE;
            double sampleRate = fmt.nSamplesPerSec > 0 ? fmt.nSamplesPerSec : 48000.0;

            const int BANDS = 20;
            double[] minFreq = new double[BANDS];
            double[] maxFreq = new double[BANDS];
            for (int b = 0; b < BANDS; b++)
            {
                double f0 = 45.0 * Math.Pow(12000.0 / 45.0, (double)b / BANDS);
                double f1 = 45.0 * Math.Pow(12000.0 / 45.0, (double)(b + 1) / BANDS);
                minFreq[b] = f0;
                maxFreq[b] = f1;
            }
            float[] smoothBands = new float[BANDS];

            var stopRequested = false;
            var musicAllowed = true;
            var allowHint = "";
            bool forceMeter = true;
            var cmdWake = new AutoResetEvent(false);
            var stopThread = new Thread(() =>
            {
                try
                {
                    string line;
                    while ((line = Console.In.ReadLine()) != null)
                    {
                        string cmdLine = line.Trim();
                        if (cmdLine.Equals("STOP", StringComparison.OrdinalIgnoreCase))
                        {
                            stopRequested = true;
                            cmdWake.Set();
                            break;
                        }
                        if (cmdLine.Equals("PAUSE", StringComparison.OrdinalIgnoreCase) || cmdLine.Equals("ALLOW:NONE", StringComparison.OrdinalIgnoreCase))
                        {
                            musicAllowed = false;
                            allowHint = "";
                            cmdWake.Set();
                        }
                        else if (cmdLine.StartsWith("ALLOW:", StringComparison.OrdinalIgnoreCase))
                        {
                            allowHint = cmdLine.Substring(6).Trim().ToLowerInvariant();
                            musicAllowed = allowHint != "none";
                            forceMeter = true;
                            cmdWake.Set();
                        }
                    }
                    stopRequested = true;
                    cmdWake.Set();
                }
                catch { stopRequested = true; cmdWake.Set(); }
            }) { IsBackground = true };
            stopThread.Start();

            string[] nonMusicProcs = new[] { "discord", "teamspeak", "ts3client", "zoom", "skype", "mumble", "ventrilo", "telegram", "whatsapp", "viber", "obs64", "obs32", "streamlabs" };
            string[] knownMusicProcs = new[] { "spotify", "yandexmusic", "applemusic", "itunes", "aimp", "winamp", "foobar2000", "musicbee", "vlc", "potplayermini", "audacious" };
            IAudioSessionManager2 sesMgr = null;
            try
            {
                object mgrObj;
                Guid iidMgr = IID_IAudioSessionManager2;
                if (dev.Activate(ref iidMgr, 23, IntPtr.Zero, out mgrObj) == 0 && mgrObj != null)
                    sesMgr = (IAudioSessionManager2)mgrObj;
            }
            catch { sesMgr = null; }

            var procNameCache = new Dictionary<int, string>();
            int meterTick = 0;
            float musicGate = 1.0f;
            byte[] rawBuf = new byte[4096];
            var outSb = new StringBuilder(160);
            const string ZERO_LINE = "0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000";
            bool zeroEmitted = false;
            int idleFrames = 0;

            while (!stopRequested)
            {
                int sleepMs = !musicAllowed ? 150 : (zeroEmitted ? 75 : 33);
                cmdWake.WaitOne(sleepMs);
                if (stopRequested) break;
                if (!musicAllowed)
                {
                    musicGate = 0.0f;
                    // Drain stale WASAPI loopback frames cleanly without running DFT
                    uint pktSkip;
                    while (cap.GetNextPacketSize(out pktSkip) == 0 && pktSkip > 0)
                    {
                        IntPtr pD;
                        uint nf, fl;
                        ulong dp, q;
                        if (cap.GetBuffer(out pD, out nf, out fl, out dp, out q) != 0) break;
                        cap.ReleaseBuffer(nf);
                    }
                    if (!zeroEmitted)
                    {
                        for (int b = 0; b < BANDS; b++) smoothBands[b] = 0.0f;
                        Console.WriteLine(ZERO_LINE);
                        zeroEmitted = true;
                    }
                    continue;
                }
                else if (sesMgr != null && (forceMeter || (meterTick++ % 3 == 0)))
                {
                    forceMeter = false;
                    float musicPeak = 0.0f;
                    float otherPeak = 0.0f;
                    bool foundMusicSession = false;
                    try
                    {
                        IAudioSessionEnumerator sesEnum;
                        if (sesMgr.GetSessionEnumerator(out sesEnum) == 0 && sesEnum != null)
                        {
                            int cnt = 0;
                            sesEnum.GetCount(out cnt);
                            for (int i = 0; i < cnt; i++)
                            {
                                IAudioSessionControl sc;
                                if (sesEnum.GetSession(i, out sc) != 0 || sc == null) continue;
                                try
                                {
                                    int st = 0;
                                    sc.GetState(out st);
                                    if (st != 1) continue; // Only active sessions
                                    var sc2 = sc as IAudioSessionControl2;
                                    var meter = sc as IAudioMeterInformation;
                                    if (sc2 == null || meter == null) continue;
                                    float pk = 0.0f;
                                    meter.GetPeakValue(out pk);
                                    if (sc2.IsSystemSoundsSession() == 0)
                                    {
                                        otherPeak = Math.Max(otherPeak, pk);
                                        continue;
                                    }
                                    uint pid = 0;
                                    sc2.GetProcessId(out pid);
                                    if (pid == 0) continue;
                                    string pname;
                                    if (!procNameCache.TryGetValue((int)pid, out pname))
                                    {
                                        try { pname = (Process.GetProcessById((int)pid).ProcessName ?? "").ToLowerInvariant(); }
                                        catch { pname = ""; }
                                        procNameCache[(int)pid] = pname;
                                    }
                                    bool isVoiceOrComms = false;
                                    for (int j = 0; j < nonMusicProcs.Length; j++)
                                    {
                                        if (pname.IndexOf(nonMusicProcs[j], StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            isVoiceOrComms = true;
                                            break;
                                        }
                                    }
                                    if (isVoiceOrComms)
                                    {
                                        otherPeak = Math.Max(otherPeak, pk);
                                        continue;
                                    }
                                    bool isMusicProc = false;
                                    if (!string.IsNullOrEmpty(allowHint) && allowHint != "any")
                                    {
                                        isMusicProc = pname.IndexOf(allowHint, StringComparison.OrdinalIgnoreCase) >= 0;
                                    }
                                    else
                                    {
                                        for (int j = 0; j < knownMusicProcs.Length; j++)
                                        {
                                            if (pname.IndexOf(knownMusicProcs[j], StringComparison.OrdinalIgnoreCase) >= 0)
                                            {
                                                isMusicProc = true;
                                                break;
                                            }
                                        }
                                    }
                                    if (isMusicProc)
                                    {
                                        foundMusicSession = true;
                                        musicPeak = Math.Max(musicPeak, pk);
                                    }
                                    else
                                    {
                                        otherPeak = Math.Max(otherPeak, pk);
                                    }
                                }
                                finally
                                {
                                    Marshal.ReleaseComObject(sc);
                                }
                            }
                            Marshal.ReleaseComObject(sesEnum);
                        }
                    }
                    catch { }

                    if (foundMusicSession)
                    {
                        if (musicPeak < 0.002f)
                            musicGate = 0.0f;
                        else
                            musicGate = Math.Max(0.15f, Math.Min(1.0f, musicPeak / Math.Max(1e-4f, musicPeak + otherPeak * 0.85f)));
                    }
                    else if (!string.IsNullOrEmpty(allowHint) && allowHint != "any")
                    {
                        // Active music session specified by Python is not emitting audio yet -> mute waves
                        musicGate = otherPeak > 0.004f ? 0.0f : 0.0f;
                    }
                    else
                    {
                        musicGate = otherPeak > 0.004f ? 0.0f : 1.0f;
                    }
                    if (procNameCache.Count > 128) procNameCache.Clear();
                }
                uint pktSize;
                bool hasAudio = false;
                while (cap.GetNextPacketSize(out pktSize) == 0 && pktSize > 0)
                {
                    IntPtr pData;
                    uint numFrames, flags;
                    ulong devPos, qpc;
                    if (cap.GetBuffer(out pData, out numFrames, out flags, out devPos, out qpc) != 0) break;
                    bool silent = (flags & 0x2) != 0;
                    if (!silent && pData != IntPtr.Zero && numFrames > 0)
                    {
                        hasAudio = true;
                        int frameBytes = channels * bytesPerSample;
                        int needBytes = (int)numFrames * frameBytes;
                        if (rawBuf.Length < needBytes) rawBuf = new byte[Math.Max(needBytes, rawBuf.Length * 2)];
                        Marshal.Copy(pData, rawBuf, 0, needBytes);
                        for (int f = 0; f < numFrames; f += 1)
                        {
                            float sample = 0f;
                            int off = f * frameBytes;
                            if (isFloat && bytesPerSample == 4)
                                sample = BitConverter.ToSingle(rawBuf, off);
                            else if (bytesPerSample == 2)
                                sample = BitConverter.ToInt16(rawBuf, off) / 32768f;
                            ring[ringPos] = sample;
                            ringPos = (ringPos + 1) & (N - 1);
                        }
                    }
                    cap.ReleaseBuffer(numFrames);
                }

                if ((!hasAudio || musicGate <= 0.001f) && zeroEmitted)
                {
                    continue;
                }

                // Compute Goertzel/DFT power on N samples
                outSb.Clear();
                float maxBand = 0.0f;
                for (int b = 0; b < BANDS; b++)
                {
                    int kStart = Math.Max(1, (int)Math.Floor(minFreq[b] * N / sampleRate));
                    int kEnd = Math.Min(N / 2 - 1, Math.Max(kStart, (int)Math.Ceiling(maxFreq[b] * N / sampleRate)));
                    double energy = 0.0;
                    if (hasAudio && musicGate > 0.001f)
                    {
                        int bins = 0;
                        for (int k = kStart; k <= kEnd; k += Math.Max(1, (kEnd - kStart) / 3))
                        {
                            double omega = 2.0 * Math.PI * k / N;
                            double re = 0.0, im = 0.0;
                            for (int n = 0; n < N; n += 2)
                            {
                                float s = ring[(ringPos + n) & (N - 1)];
                                double a = omega * n;
                                re += s * Math.Cos(a);
                                im -= s * Math.Sin(a);
                            }
                            energy += Math.Sqrt(re * re + im * im) / (N * 0.25);
                            bins++;
                        }
                        if (bins > 0) energy /= bins;
                    }
                    float target = (musicGate <= 0.001f) ? 0.0f : (float)Math.Min(1.0, Math.Pow(energy * 2.4 * musicGate, 0.7));
                    smoothBands[b] = target > smoothBands[b]
                        ? smoothBands[b] * 0.35f + target * 0.65f
                        : smoothBands[b] * 0.72f + target * 0.28f;
                    if (smoothBands[b] > maxBand) maxBand = smoothBands[b];
                    if (b > 0) outSb.Append(',');
                    outSb.Append(smoothBands[b].ToString("F3", CultureInfo.InvariantCulture));
                }
                if (maxBand < 0.002f)
                {
                    idleFrames++;
                    if (idleFrames >= 4)
                    {
                        for (int b = 0; b < BANDS; b++) smoothBands[b] = 0.0f;
                        Console.WriteLine(ZERO_LINE);
                        zeroEmitted = true;
                        continue;
                    }
                }
                else
                {
                    idleFrames = 0;
                    zeroEmitted = false;
                }
                Console.WriteLine(outSb.ToString());
            }

            try { cmdWake.Dispose(); } catch { }
            if (sesMgr != null) Marshal.ReleaseComObject(sesMgr);

            client.Stop();
            Marshal.FreeCoTaskMem(pFmt);
            Marshal.ReleaseComObject(cap);
            Marshal.ReleaseComObject(client);
            Marshal.ReleaseComObject(dev);
        }
    }
    #endregion

    #region Monitor Brightness (WMI + dxva2 fallback)
    internal static class BrightnessBridge
    {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct PHYSICAL_MONITOR
        {
            public IntPtr hPhysicalMonitor;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
            public string szPhysicalMonitorDescription;
        }

        [DllImport("user32.dll")]
        private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint dwFlags);

        [DllImport("user32.dll")]
        private static extern IntPtr GetDesktopWindow();

        [DllImport("dxva2.dll", SetLastError = true)]
        private static extern bool GetNumberOfPhysicalMonitorsFromHMONITOR(IntPtr hMonitor, out uint pdwNumberOfPhysicalMonitors);

        [DllImport("dxva2.dll", SetLastError = true)]
        private static extern bool GetPhysicalMonitorsFromHMONITOR(IntPtr hMonitor, uint dwPhysicalMonitorArraySize, [Out] PHYSICAL_MONITOR[] pPhysicalMonitorArray);

        [DllImport("dxva2.dll", SetLastError = true)]
        private static extern bool DestroyPhysicalMonitors(uint dwPhysicalMonitorArraySize, [In] PHYSICAL_MONITOR[] pPhysicalMonitorArray);

        [DllImport("dxva2.dll", SetLastError = true)]
        private static extern bool GetMonitorBrightness(IntPtr hMonitor, out uint pdwMinimumBrightness, out uint pdwCurrentBrightness, out uint pdwMaximumBrightness);

        [DllImport("dxva2.dll", SetLastError = true)]
        private static extern bool SetMonitorBrightness(IntPtr hMonitor, uint dwNewBrightness);

        private static int GetDdcBrightness()
        {
            try
            {
                IntPtr hMon = MonitorFromWindow(GetDesktopWindow(), 1 /* MONITOR_DEFAULTTOPRIMARY */);
                uint count;
                if (!GetNumberOfPhysicalMonitorsFromHMONITOR(hMon, out count) || count == 0) return -1;
                var arr = new PHYSICAL_MONITOR[count];
                if (!GetPhysicalMonitorsFromHMONITOR(hMon, count, arr)) return -1;
                try
                {
                    uint minB, curB, maxB;
                    if (GetMonitorBrightness(arr[0].hPhysicalMonitor, out minB, out curB, out maxB) && maxB > minB)
                    {
                        return (int)Math.Round((curB - minB) * 100.0 / (maxB - minB));
                    }
                }
                finally
                {
                    DestroyPhysicalMonitors(count, arr);
                }
            }
            catch { }
            return -1;
        }

        private static bool SetDdcBrightness(int level)
        {
            try
            {
                IntPtr hMon = MonitorFromWindow(GetDesktopWindow(), 1);
                uint count;
                if (!GetNumberOfPhysicalMonitorsFromHMONITOR(hMon, out count) || count == 0) return false;
                var arr = new PHYSICAL_MONITOR[count];
                if (!GetPhysicalMonitorsFromHMONITOR(hMon, count, arr)) return false;
                try
                {
                    bool any = false;
                    for (uint i = 0; i < count; i++)
                    {
                        uint minB, curB, maxB;
                        if (GetMonitorBrightness(arr[i].hPhysicalMonitor, out minB, out curB, out maxB) && maxB > minB)
                        {
                            uint val = (uint)Math.Round(minB + (maxB - minB) * (level / 100.0));
                            if (SetMonitorBrightness(arr[i].hPhysicalMonitor, val)) any = true;
                        }
                    }
                    return any;
                }
                finally
                {
                    DestroyPhysicalMonitors(count, arr);
                }
            }
            catch { return false; }
        }

        public static int GetBrightness()
        {
            try
            {
                using (var searcher = new ManagementObjectSearcher("root\\WMI", "SELECT CurrentBrightness FROM WmiMonitorBrightness"))
                {
                    foreach (ManagementObject obj in searcher.Get())
                    {
                        return Convert.ToInt32(obj["CurrentBrightness"], CultureInfo.InvariantCulture);
                    }
                }
            }
            catch { }
            return GetDdcBrightness();
        }

        public static void SetBrightness(int level)
        {
            int clamped = Math.Max(0, Math.Min(100, level));
            try
            {
                using (var searcher = new ManagementObjectSearcher("root\\WMI", "SELECT * FROM WmiMonitorBrightnessMethods"))
                {
                    foreach (ManagementObject obj in searcher.Get())
                    {
                        obj.InvokeMethod("WmiSetBrightness", new object[] { (uint)1, (byte)clamped });
                        return;
                    }
                }
            }
            catch { }
            SetDdcBrightness(clamped);
        }
    }
    #endregion

    #region Windows Media Foundation MP4 H.264 Screen Recorder
    internal static class Mp4Recorder
    {
        private static readonly Guid MFMediaType_Video = new Guid("73646976-0000-0010-8000-00AA00389B71");
        private static readonly Guid MFVideoFormat_H264 = new Guid("34363248-0000-0010-8000-00AA00389B71");
        private static readonly Guid MFVideoFormat_RGB32 = new Guid("00000016-0000-0010-8000-00AA00389B71");
        private static readonly Guid MF_MT_MAJOR_TYPE = new Guid("48eba18e-f8c9-4687-bf11-0a74c9f96a8f");
        private static readonly Guid MF_MT_SUBTYPE = new Guid("f7e34c9a-42e8-4714-b74b-cb29d72c35e5");
        private static readonly Guid MF_MT_AVG_BITRATE = new Guid("20332624-fb0d-4d9e-bd0d-cbf6786c102e");
        private static readonly Guid MF_MT_INTERLACE_MODE = new Guid("e2724bb8-e676-4806-b4b2-a8d6efb44ccd");
        private static readonly Guid MF_MT_FRAME_SIZE = new Guid("1652c33d-d6b2-4012-b834-72030849a37d");
        private static readonly Guid MF_MT_FRAME_RATE = new Guid("c459a2e8-3d2c-4e44-b132-fee5156c7bb0");
        private static readonly Guid MF_MT_PIXEL_ASPECT_RATIO = new Guid("c6376a1e-8d0a-4027-be45-6d9a0ad39bb6");
        private static readonly Guid MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS = new Guid("a634a91c-822b-41b9-a494-4de4643612b0");

        private static readonly Guid MFMediaType_Audio = new Guid("73647561-0000-0010-8000-00AA00389B71");
        private static readonly Guid MFAudioFormat_AAC = new Guid("00001610-0000-0010-8000-00AA00389B71");
        private static readonly Guid MFAudioFormat_PCM = new Guid("00000001-0000-0010-8000-00AA00389B71");
        private static readonly Guid MF_MT_AUDIO_NUM_CHANNELS = new Guid("37e48bf5-645e-4c5b-89de-ada9e29b696a");
        private static readonly Guid MF_MT_AUDIO_SAMPLES_PER_SECOND = new Guid("5faeeae7-0290-4c31-9e8a-c534f68d9dba");
        private static readonly Guid MF_MT_AUDIO_BITS_PER_SAMPLE = new Guid("f2deb57f-40fa-4764-aa33-ed4f2d1ff669");
        private static readonly Guid MF_MT_AUDIO_AVG_BYTES_PER_SECOND = new Guid("1aab75c8-cfef-451c-ab95-ac034b8e1731");
        private static readonly Guid MF_MT_AUDIO_BLOCK_ALIGNMENT = new Guid("322de230-9eeb-43bd-ab7a-ff412251541d");
        private static readonly Guid MF_MT_AAC_PAYLOAD_TYPE = new Guid("bfbabe79-7434-4d1c-94f0-72a3b9e17188");
        private static readonly Guid MF_MT_AAC_AUDIO_PROFILE_LEVEL_INDICATION = new Guid("7632f0e6-95ff-4361-95c3-5f0d542d4149");

        [DllImport("mfplat.dll")] private static extern int MFStartup(int version, int dwFlags);
        [DllImport("mfplat.dll")] private static extern int MFShutdown();
        [DllImport("mfplat.dll")] private static extern int MFCreateMediaType(out IMFMediaType ppMFType);
        [DllImport("mfplat.dll")] private static extern int MFCreateAttributes(out IMFAttributes ppMFAttributes, uint cInitialSize);
        [DllImport("mfplat.dll")] private static extern int MFCreateMemoryBuffer(uint cbMaxLength, out IMFMediaBuffer ppBuffer);
        [DllImport("mfplat.dll")] private static extern int MFCreateSample(out IMFSample ppIMFSample);
        [DllImport("mfreadwrite.dll")] private static extern int MFCreateSinkWriterFromURL([MarshalAs(UnmanagedType.LPWStr)] string pwszOutputURL, IntPtr pByteStream, IMFAttributes pAttributes, out IMFSinkWriter ppSinkWriter);

        [ComImport, Guid("2cd2d921-c447-44a7-a13c-4adabfc247e3"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        internal interface IMFAttributes
        {
            [PreserveSig] int GetItem(ref Guid guidKey, IntPtr pValue);
            [PreserveSig] int GetItemType(ref Guid guidKey, out int pType);
            [PreserveSig] int CompareItem(ref Guid guidKey, IntPtr Value, out bool pbResult);
            [PreserveSig] int Compare(IMFAttributes pTheirs, int MatchType, out bool pbResult);
            [PreserveSig] int GetUINT32(ref Guid guidKey, out uint punValue);
            [PreserveSig] int GetUINT64(ref Guid guidKey, out ulong punValue);
            [PreserveSig] int GetDouble(ref Guid guidKey, out double pfValue);
            [PreserveSig] int GetGUID(ref Guid guidKey, out Guid pguidValue);
            [PreserveSig] int GetStringLength(ref Guid guidKey, out uint pcchLength);
            [PreserveSig] int GetString(ref Guid guidKey, StringBuilder pwszValue, uint cchBufSize, out uint pcchLength);
            [PreserveSig] int GetAllocatedString(ref Guid guidKey, out IntPtr ppwszValue, out uint pcchLength);
            [PreserveSig] int GetBlobSize(ref Guid guidKey, out uint pcbBlobSize);
            [PreserveSig] int GetBlob(ref Guid guidKey, byte[] pBuf, uint cbBufSize, out uint pcbBlobSize);
            [PreserveSig] int GetAllocatedBlob(ref Guid guidKey, out IntPtr ppBuf, out uint pcbSize);
            [PreserveSig] int GetUnknown(ref Guid guidKey, ref Guid riid, out IntPtr ppv);
            [PreserveSig] int SetItem(ref Guid guidKey, IntPtr Value);
            [PreserveSig] int DeleteItem(ref Guid guidKey);
            [PreserveSig] int DeleteAllItems();
            [PreserveSig] int SetUINT32(ref Guid guidKey, uint unValue);
            [PreserveSig] int SetUINT64(ref Guid guidKey, ulong unValue);
            [PreserveSig] int SetDouble(ref Guid guidKey, double fValue);
            [PreserveSig] int SetGUID(ref Guid guidKey, ref Guid guidValue);
            [PreserveSig] int SetString(ref Guid guidKey, [MarshalAs(UnmanagedType.LPWStr)] string wszValue);
            [PreserveSig] int SetBlob(ref Guid guidKey, byte[] pBuf, uint cbBufSize);
            [PreserveSig] int SetUnknown(ref Guid guidKey, [MarshalAs(UnmanagedType.IUnknown)] object pUnknown);
            [PreserveSig] int LockStore();
            [PreserveSig] int UnlockStore();
            [PreserveSig] int GetCount(out uint pcItems);
            [PreserveSig] int GetItemByIndex(uint unIndex, out Guid pguidKey, IntPtr pValue);
            [PreserveSig] int CopyAllItems(IMFAttributes pDest);
        }

        [ComImport, Guid("44ae0fa8-ea31-4109-8d2e-4cae4997c555"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        internal interface IMFMediaType : IMFAttributes
        {
            [PreserveSig] new int GetItem(ref Guid guidKey, IntPtr pValue);
            [PreserveSig] new int GetItemType(ref Guid guidKey, out int pType);
            [PreserveSig] new int CompareItem(ref Guid guidKey, IntPtr Value, out bool pbResult);
            [PreserveSig] new int Compare(IMFAttributes pTheirs, int MatchType, out bool pbResult);
            [PreserveSig] new int GetUINT32(ref Guid guidKey, out uint punValue);
            [PreserveSig] new int GetUINT64(ref Guid guidKey, out ulong punValue);
            [PreserveSig] new int GetDouble(ref Guid guidKey, out double pfValue);
            [PreserveSig] new int GetGUID(ref Guid guidKey, out Guid pguidValue);
            [PreserveSig] new int GetStringLength(ref Guid guidKey, out uint pcchLength);
            [PreserveSig] new int GetString(ref Guid guidKey, StringBuilder pwszValue, uint cchBufSize, out uint pcchLength);
            [PreserveSig] new int GetAllocatedString(ref Guid guidKey, out IntPtr ppwszValue, out uint pcchLength);
            [PreserveSig] new int GetBlobSize(ref Guid guidKey, out uint pcbBlobSize);
            [PreserveSig] new int GetBlob(ref Guid guidKey, byte[] pBuf, uint cbBufSize, out uint pcbBlobSize);
            [PreserveSig] new int GetAllocatedBlob(ref Guid guidKey, out IntPtr ppBuf, out uint pcbSize);
            [PreserveSig] new int GetUnknown(ref Guid guidKey, ref Guid riid, out IntPtr ppv);
            [PreserveSig] new int SetItem(ref Guid guidKey, IntPtr Value);
            [PreserveSig] new int DeleteItem(ref Guid guidKey);
            [PreserveSig] new int DeleteAllItems();
            [PreserveSig] new int SetUINT32(ref Guid guidKey, uint unValue);
            [PreserveSig] new int SetUINT64(ref Guid guidKey, ulong unValue);
            [PreserveSig] new int SetDouble(ref Guid guidKey, double fValue);
            [PreserveSig] new int SetGUID(ref Guid guidKey, ref Guid guidValue);
            [PreserveSig] new int SetString(ref Guid guidKey, [MarshalAs(UnmanagedType.LPWStr)] string wszValue);
            [PreserveSig] new int SetBlob(ref Guid guidKey, byte[] pBuf, uint cbBufSize);
            [PreserveSig] new int SetUnknown(ref Guid guidKey, [MarshalAs(UnmanagedType.IUnknown)] object pUnknown);
            [PreserveSig] new int LockStore();
            [PreserveSig] new int UnlockStore();
            [PreserveSig] new int GetCount(out uint pcItems);
            [PreserveSig] new int GetItemByIndex(uint unIndex, out Guid pguidKey, IntPtr pValue);
            [PreserveSig] new int CopyAllItems(IMFAttributes pDest);
            [PreserveSig] int GetMajorType(out Guid pguidMajorType);
            [PreserveSig] int IsCompressedFormat([MarshalAs(UnmanagedType.Bool)] out bool pfCompressed);
            [PreserveSig] int IsEqual(IMFMediaType pIMediaType, out uint pdwFlags);
            [PreserveSig] int GetRepresentation(Guid guidRepresentation, out IntPtr ppvRepresentation);
            [PreserveSig] int FreeRepresentation(Guid guidRepresentation, IntPtr pvRepresentation);
        }

        [ComImport, Guid("045FA593-8799-42b8-BC8D-8968C6453507"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        internal interface IMFMediaBuffer
        {
            [PreserveSig] int Lock(out IntPtr ppbBuffer, out uint pcbMaxLength, out uint pcbCurrentLength);
            [PreserveSig] int Unlock();
            [PreserveSig] int GetCurrentLength(out uint pcbCurrentLength);
            [PreserveSig] int SetCurrentLength(uint cbCurrentLength);
        }

        [ComImport, Guid("c40a00f2-b93a-4d80-ae8c-5a1c634f58e4"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        internal interface IMFSample : IMFAttributes
        {
            [PreserveSig] new int GetItem(ref Guid guidKey, IntPtr pValue);
            [PreserveSig] new int GetItemType(ref Guid guidKey, out int pType);
            [PreserveSig] new int CompareItem(ref Guid guidKey, IntPtr Value, out bool pbResult);
            [PreserveSig] new int Compare(IMFAttributes pTheirs, int MatchType, out bool pbResult);
            [PreserveSig] new int GetUINT32(ref Guid guidKey, out uint punValue);
            [PreserveSig] new int GetUINT64(ref Guid guidKey, out ulong punValue);
            [PreserveSig] new int GetDouble(ref Guid guidKey, out double pfValue);
            [PreserveSig] new int GetGUID(ref Guid guidKey, out Guid pguidValue);
            [PreserveSig] new int GetStringLength(ref Guid guidKey, out uint pcchLength);
            [PreserveSig] new int GetString(ref Guid guidKey, StringBuilder pwszValue, uint cchBufSize, out uint pcchLength);
            [PreserveSig] new int GetAllocatedString(ref Guid guidKey, out IntPtr ppwszValue, out uint pcchLength);
            [PreserveSig] new int GetBlobSize(ref Guid guidKey, out uint pcbBlobSize);
            [PreserveSig] new int GetBlob(ref Guid guidKey, byte[] pBuf, uint cbBufSize, out uint pcbBlobSize);
            [PreserveSig] new int GetAllocatedBlob(ref Guid guidKey, out IntPtr ppBuf, out uint pcbSize);
            [PreserveSig] new int GetUnknown(ref Guid guidKey, ref Guid riid, out IntPtr ppv);
            [PreserveSig] new int SetItem(ref Guid guidKey, IntPtr Value);
            [PreserveSig] new int DeleteItem(ref Guid guidKey);
            [PreserveSig] new int DeleteAllItems();
            [PreserveSig] new int SetUINT32(ref Guid guidKey, uint unValue);
            [PreserveSig] new int SetUINT64(ref Guid guidKey, ulong unValue);
            [PreserveSig] new int SetDouble(ref Guid guidKey, double fValue);
            [PreserveSig] new int SetGUID(ref Guid guidKey, ref Guid guidValue);
            [PreserveSig] new int SetString(ref Guid guidKey, [MarshalAs(UnmanagedType.LPWStr)] string wszValue);
            [PreserveSig] new int SetBlob(ref Guid guidKey, byte[] pBuf, uint cbBufSize);
            [PreserveSig] new int SetUnknown(ref Guid guidKey, [MarshalAs(UnmanagedType.IUnknown)] object pUnknown);
            [PreserveSig] new int LockStore();
            [PreserveSig] new int UnlockStore();
            [PreserveSig] new int GetCount(out uint pcItems);
            [PreserveSig] new int GetItemByIndex(uint unIndex, out Guid pguidKey, IntPtr pValue);
            [PreserveSig] new int CopyAllItems(IMFAttributes pDest);
            [PreserveSig] int GetSampleFlags(out uint pdwSampleFlags);
            [PreserveSig] int SetSampleFlags(uint dwSampleFlags);
            [PreserveSig] int GetSampleTime(out long phnsSampleTime);
            [PreserveSig] int SetSampleTime(long hnsSampleTime);
            [PreserveSig] int GetSampleDuration(out long phnsSampleDuration);
            [PreserveSig] int SetSampleDuration(long hnsSampleDuration);
            [PreserveSig] int GetBufferCount(out uint pdwBufferCount);
            [PreserveSig] int GetBufferByIndex(uint dwIndex, out IMFMediaBuffer ppBuffer);
            [PreserveSig] int ConvertToContiguousBuffer(out IMFMediaBuffer ppBuffer);
            [PreserveSig] int AddBuffer(IMFMediaBuffer pBuffer);
        }

        [ComImport, Guid("3137f1cd-fe5e-4805-a5d8-fb477448cb3d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        internal interface IMFSinkWriter
        {
            [PreserveSig] int AddStream(IMFMediaType pTargetMediaType, out uint pdwStreamIndex);
            [PreserveSig] int SetInputMediaType(uint dwStreamIndex, IMFMediaType pInputMediaType, IMFAttributes pEncodingParameters);
            [PreserveSig] int BeginWriting();
            [PreserveSig] int WriteSample(uint dwStreamIndex, IMFSample pSample);
            [PreserveSig] int SendStreamTick(uint dwStreamIndex, long llTimestamp);
            [PreserveSig] int PlaceMarker(uint dwStreamIndex, IntPtr pvContext);
            [PreserveSig] int NotifyEndOfSegment(uint dwStreamIndex);
            [PreserveSig] int Flush(uint dwStreamIndex);
            [PreserveSig] int Finalize();
        }

        [DllImport("kernel32.dll", EntryPoint = "RtlMoveMemory")]
        private static extern void CopyMemory(IntPtr dest, IntPtr src, int count);

        private static ulong PackPair(uint hi, uint lo)
        {
            return ((ulong)hi << 32) | lo;
        }

        private static void WriteFrameBytes(IMFSinkWriter writer, uint streamIndex, byte[] bottomUpBgra, uint bufBytes, long frameNum, long frameDur)
        {
            IMFMediaBuffer mb;
            Marshal.ThrowExceptionForHR(MFCreateMemoryBuffer(bufBytes, out mb));
            IntPtr pDest;
            uint maxL, curL;
            Marshal.ThrowExceptionForHR(mb.Lock(out pDest, out maxL, out curL));
            try
            {
                Marshal.Copy(bottomUpBgra, 0, pDest, (int)bufBytes);
            }
            finally
            {
                mb.Unlock();
            }
            mb.SetCurrentLength(bufBytes);

            IMFSample sample;
            Marshal.ThrowExceptionForHR(MFCreateSample(out sample));
            sample.AddBuffer(mb);
            sample.SetSampleTime(frameNum * frameDur);
            sample.SetSampleDuration(frameDur);
            Marshal.ThrowExceptionForHR(writer.WriteSample(streamIndex, sample));

            Marshal.ReleaseComObject(sample);
            Marshal.ReleaseComObject(mb);
        }

        private static IMFSinkWriter CreateH264Writer(
            int width, int height, int fps, string outPath,
            bool wantAudio, out uint videoStreamIndex, out uint audioStreamIndex, out bool hasAudio)
        {
            audioStreamIndex = 0;
            hasAudio = false;
            IMFAttributes attrs;
            Marshal.ThrowExceptionForHR(MFCreateAttributes(out attrs, 1));
            Guid hwKey = MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS;
            attrs.SetUINT32(ref hwKey, 1);

            IMFSinkWriter writer;
            Marshal.ThrowExceptionForHR(MFCreateSinkWriterFromURL(outPath, IntPtr.Zero, attrs, out writer));

            IMFMediaType outType;
            Marshal.ThrowExceptionForHR(MFCreateMediaType(out outType));
            Guid majorKey = MF_MT_MAJOR_TYPE, majorVal = MFMediaType_Video;
            Guid subKey = MF_MT_SUBTYPE, subVal = MFVideoFormat_H264;
            Guid bitrateKey = MF_MT_AVG_BITRATE;
            Guid interlaceKey = MF_MT_INTERLACE_MODE;
            Guid sizeKey = MF_MT_FRAME_SIZE;
            Guid rateKey = MF_MT_FRAME_RATE;
            Guid parKey = MF_MT_PIXEL_ASPECT_RATIO;

            outType.SetGUID(ref majorKey, ref majorVal);
            outType.SetGUID(ref subKey, ref subVal);
            outType.SetUINT32(ref bitrateKey, (uint)(Math.Max(2500000, width * height * 4)));
            outType.SetUINT32(ref interlaceKey, 2);
            outType.SetUINT64(ref sizeKey, PackPair((uint)width, (uint)height));
            outType.SetUINT64(ref rateKey, PackPair((uint)fps, 1));
            outType.SetUINT64(ref parKey, PackPair(1, 1));
            Marshal.ThrowExceptionForHR(writer.AddStream(outType, out videoStreamIndex));

            IMFMediaType inType;
            Marshal.ThrowExceptionForHR(MFCreateMediaType(out inType));
            Guid rgb32Val = MFVideoFormat_RGB32;
            inType.SetGUID(ref majorKey, ref majorVal);
            inType.SetGUID(ref subKey, ref rgb32Val);
            inType.SetUINT32(ref interlaceKey, 2);
            inType.SetUINT64(ref sizeKey, PackPair((uint)width, (uint)height));
            inType.SetUINT64(ref rateKey, PackPair((uint)fps, 1));
            inType.SetUINT64(ref parKey, PackPair(1, 1));
            Marshal.ThrowExceptionForHR(writer.SetInputMediaType(videoStreamIndex, inType, null));

            if (wantAudio)
            {
                try
                {
                    IMFMediaType aOut;
                    Marshal.ThrowExceptionForHR(MFCreateMediaType(out aOut));
                    Guid audMaj = MFMediaType_Audio;
                    Guid aacSub = MFAudioFormat_AAC;
                    Guid chK = MF_MT_AUDIO_NUM_CHANNELS, srK = MF_MT_AUDIO_SAMPLES_PER_SECOND;
                    Guid bpsK = MF_MT_AUDIO_BITS_PER_SAMPLE, avgK = MF_MT_AUDIO_AVG_BYTES_PER_SECOND;
                    Guid ptK = MF_MT_AAC_PAYLOAD_TYPE, lvlK = MF_MT_AAC_AUDIO_PROFILE_LEVEL_INDICATION;
                    aOut.SetGUID(ref majorKey, ref audMaj);
                    aOut.SetGUID(ref subKey, ref aacSub);
                    aOut.SetUINT32(ref chK, 2);
                    aOut.SetUINT32(ref srK, 48000);
                    aOut.SetUINT32(ref bpsK, 16);
                    aOut.SetUINT32(ref avgK, 20000);
                    aOut.SetUINT32(ref ptK, 0);
                    aOut.SetUINT32(ref lvlK, 0x29);
                    if (writer.AddStream(aOut, out audioStreamIndex) == 0)
                    {
                        IMFMediaType aIn;
                        Marshal.ThrowExceptionForHR(MFCreateMediaType(out aIn));
                        Guid pcmSub = MFAudioFormat_PCM;
                        Guid blkK = MF_MT_AUDIO_BLOCK_ALIGNMENT;
                        aIn.SetGUID(ref majorKey, ref audMaj);
                        aIn.SetGUID(ref subKey, ref pcmSub);
                        aIn.SetUINT32(ref chK, 2);
                        aIn.SetUINT32(ref srK, 48000);
                        aIn.SetUINT32(ref bpsK, 16);
                        aIn.SetUINT32(ref blkK, 4);
                        aIn.SetUINT32(ref avgK, 48000 * 4);
                        if (writer.SetInputMediaType(audioStreamIndex, aIn, null) == 0)
                        {
                            hasAudio = true;
                        }
                    }
                }
                catch
                {
                    hasAudio = false;
                }
            }

            Marshal.ThrowExceptionForHR(writer.BeginWriting());
            return writer;
        }

        private static void DrainCaptureIntoRing(
            IAudioCaptureClient cap, WAVEFORMATEX fmt, float[] ring, ref int writePos, int mask)
        {
            if (cap == null) return;
            uint pkt = 0;
            int ch = Math.Max(1, (int)fmt.nChannels);
            int bps = Math.Max(2, fmt.wBitsPerSample / 8);
            bool isFloat = fmt.wFormatTag == 3 || fmt.wFormatTag == 0xFFFE;
            int stepFrames = fmt.nSamplesPerSec > 0 ? Math.Max(1, (int)fmt.nSamplesPerSec / 48000) : 1;
            double ratio = (fmt.nSamplesPerSec > 0 ? fmt.nSamplesPerSec : 48000.0) / 48000.0;
            while (cap.GetNextPacketSize(out pkt) == 0 && pkt > 0)
            {
                IntPtr pData;
                uint frames, flags;
                ulong pos1, pos2;
                if (cap.GetBuffer(out pData, out frames, out flags, out pos1, out pos2) != 0) break;
                try
                {
                    bool silent = (flags & 2u) != 0 || pData == IntPtr.Zero;
                    int outCount = Math.Max(0, (int)Math.Round(frames / ratio));
                    for (int oi = 0; oi < outCount; oi++)
                    {
                        uint fIdx = Math.Min(frames - 1, (uint)(oi * ratio));
                        float l = 0f, r = 0f;
                        if (!silent)
                        {
                            int baseOff = (int)fIdx * ch * bps;
                            l = isFloat
                                ? BitConverter.ToSingle(new byte[] {
                                    Marshal.ReadByte(pData, baseOff),
                                    Marshal.ReadByte(pData, baseOff + 1),
                                    Marshal.ReadByte(pData, baseOff + 2),
                                    Marshal.ReadByte(pData, baseOff + 3) }, 0)
                                : (Marshal.ReadInt16(pData, baseOff) / 32768f);
                            int rOff = baseOff + (ch > 1 ? bps : 0);
                            r = isFloat
                                ? BitConverter.ToSingle(new byte[] {
                                    Marshal.ReadByte(pData, rOff),
                                    Marshal.ReadByte(pData, rOff + 1),
                                    Marshal.ReadByte(pData, rOff + 2),
                                    Marshal.ReadByte(pData, rOff + 3) }, 0)
                                : (Marshal.ReadInt16(pData, rOff) / 32768f);
                        }
                        int idxL = (writePos & mask) * 2;
                        ring[idxL] = l;
                        ring[idxL + 1] = r;
                        writePos++;
                    }
                }
                finally
                {
                    cap.ReleaseBuffer(frames);
                }
            }
        }

        public static int RecordFromStdinPipe(int width, int height, int fps, string outPath)
        {
            width = Math.Max(16, (width / 2) * 2);
            height = Math.Max(16, (height / 2) * 2);
            fps = Math.Max(10, Math.Min(60, fps));
            const int MF_VERSION = 0x00020070;
            Marshal.ThrowExceptionForHR(MFStartup(MF_VERSION, 0));
            IMFSinkWriter writer = null;
            try
            {
                uint streamIndex, dummyAudio;
                bool dummyHas;
                writer = CreateH264Writer(width, height, fps, outPath, false, out streamIndex, out dummyAudio, out dummyHas);
                Console.WriteLine("READY");
                long frameDur = 10000000L / fps;
                long frameNum = 0;
                uint bufBytes = (uint)(width * height * 4);
                byte[] frameBuf = new byte[bufBytes];
                using (Stream stdin = Console.OpenStandardInput())
                {
                    while (true)
                    {
                        int readTotal = 0;
                        while (readTotal < frameBuf.Length)
                        {
                            int n = stdin.Read(frameBuf, readTotal, frameBuf.Length - readTotal);
                            if (n <= 0) break;
                            readTotal += n;
                        }
                        if (readTotal < frameBuf.Length) break;
                        WriteFrameBytes(writer, streamIndex, frameBuf, bufBytes, frameNum, frameDur);
                        frameNum++;
                    }
                }
                if (frameNum > 0)
                {
                    Marshal.ThrowExceptionForHR(writer.Finalize());
                }
                Console.WriteLine("SAVED:" + outPath);
                return 0;
            }
            finally
            {
                if (writer != null) Marshal.ReleaseComObject(writer);
                MFShutdown();
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct WINRECT
        {
            public int Left, Top, Right, Bottom;
        }

        [DllImport("user32.dll")]
        private static extern bool IsWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool GetWindowRect(IntPtr hWnd, out WINRECT lpRect);

        [DllImport("user32.dll")]
        private static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);

        public static int RecordRegion(int x, int y, int width, int height, int fps, string outPath, string audioMode = "none", IntPtr targetHwnd = default(IntPtr))
        {
            // H.264 requires even dimensions
            width = (width / 2) * 2;
            height = (height / 2) * 2;
            if (width < 16) width = 16;
            if (height < 16) height = 16;
            fps = Math.Max(10, Math.Min(60, fps));
            string am = (audioMode ?? "none").Trim().ToLowerInvariant();
            bool useSys = am == "system" || am == "both";
            bool useMic = am == "both";

            const int MF_VERSION = 0x00020070;
            Marshal.ThrowExceptionForHR(MFStartup(MF_VERSION, 0));
            IMFSinkWriter writer = null;
            IAudioClient sysClient = null, micClient = null;
            IAudioCaptureClient sysCap = null, micCap = null;
            WAVEFORMATEX sysFmt = default(WAVEFORMATEX), micFmt = default(WAVEFORMATEX);
            try
            {
                bool sysOk = useSys && AudioBridge.TryOpenWasapiCapture(true, out sysClient, out sysCap, out sysFmt);
                bool micOk = useMic && AudioBridge.TryOpenWasapiCapture(false, out micClient, out micCap, out micFmt);

                uint streamIndex, audioStreamIndex;
                bool hasAudio;
                writer = CreateH264Writer(width, height, fps, outPath, (sysOk || micOk), out streamIndex, out audioStreamIndex, out hasAudio);
                Console.WriteLine("READY");

                bool stop = false;
                var writerLock = new object();
                var stopThread = new Thread(() =>
                {
                    try
                    {
                        string line;
                        while ((line = Console.In.ReadLine()) != null)
                        {
                            if (line.Trim().Equals("STOP", StringComparison.OrdinalIgnoreCase))
                            {
                                stop = true;
                                break;
                            }
                        }
                        stop = true;
                    }
                    catch { stop = true; }
                }) { IsBackground = true };
                stopThread.Start();

                var sw = System.Diagnostics.Stopwatch.StartNew();
                Thread audioThread = null;
                if (hasAudio && (sysOk || micOk))
                {
                    audioThread = new Thread(() =>
                    {
                        const int RING_FRAMES = 65536;
                        const int MASK = RING_FRAMES - 1;
                        float[] sysRing = new float[RING_FRAMES * 2];
                        float[] micRing = new float[RING_FRAMES * 2];
                        int sysW = 0, sysR = 0, micW = 0, micR = 0;
                        const int CHUNK_FRAMES = 960; // 20ms @ 48kHz
                        const long CHUNK_DUR_100NS = 200000L;
                        byte[] pcmBytes = new byte[CHUNK_FRAMES * 4];
                        long audioTs = 0;
                        long chunkIdx = 0;

                        while (!stop)
                        {
                            if (sysOk) DrainCaptureIntoRing(sysCap, sysFmt, sysRing, ref sysW, MASK);
                            if (micOk) DrainCaptureIntoRing(micCap, micFmt, micRing, ref micW, MASK);

                            long targetMs = chunkIdx * 20L;
                            if (sw.ElapsedMilliseconds < targetMs)
                            {
                                Thread.Sleep(5);
                                continue;
                            }

                            for (int i = 0; i < CHUNK_FRAMES; i++)
                            {
                                float l = 0f, r = 0f;
                                if (sysOk && sysR < sysW)
                                {
                                    int idx = (sysR & MASK) * 2;
                                    l += sysRing[idx];
                                    r += sysRing[idx + 1];
                                    sysR++;
                                }
                                if (micOk && micR < micW)
                                {
                                    int idx = (micR & MASK) * 2;
                                    l += micRing[idx];
                                    r += micRing[idx + 1];
                                    micR++;
                                }
                                short sL = (short)Math.Max(-32767, Math.Min(32767, (int)(l * 32767f)));
                                short sR = (short)Math.Max(-32767, Math.Min(32767, (int)(r * 32767f)));
                                int bo = i * 4;
                                pcmBytes[bo] = (byte)(sL & 0xFF);
                                pcmBytes[bo + 1] = (byte)((sL >> 8) & 0xFF);
                                pcmBytes[bo + 2] = (byte)(sR & 0xFF);
                                pcmBytes[bo + 3] = (byte)((sR >> 8) & 0xFF);
                            }

                            try
                            {
                                IMFMediaBuffer amb;
                                Marshal.ThrowExceptionForHR(MFCreateMemoryBuffer((uint)pcmBytes.Length, out amb));
                                IntPtr apDest;
                                uint aMax, aCur;
                                Marshal.ThrowExceptionForHR(amb.Lock(out apDest, out aMax, out aCur));
                                try { Marshal.Copy(pcmBytes, 0, apDest, pcmBytes.Length); }
                                finally { amb.Unlock(); }
                                amb.SetCurrentLength((uint)pcmBytes.Length);

                                IMFSample asamp;
                                Marshal.ThrowExceptionForHR(MFCreateSample(out asamp));
                                asamp.AddBuffer(amb);
                                asamp.SetSampleTime(audioTs);
                                asamp.SetSampleDuration(CHUNK_DUR_100NS);
                                lock (writerLock)
                                {
                                    writer.WriteSample(audioStreamIndex, asamp);
                                }
                                Marshal.ReleaseComObject(asamp);
                                Marshal.ReleaseComObject(amb);
                            }
                            catch { }

                            audioTs += CHUNK_DUR_100NS;
                            chunkIdx++;
                        }
                    }) { IsBackground = true };
                    audioThread.Start();
                }

                long frameDur = 10000000L / fps;
                long frameNum = 0;
                uint bufBytes = (uint)(width * height * 4);

                using (var bmp = new Bitmap(width, height, PixelFormat.Format32bppRgb))
                using (var g = Graphics.FromImage(bmp))
                {
                    while (!stop)
                    {
                        long targetMs = (frameNum * 1000L) / fps;
                        long nowMs = sw.ElapsedMilliseconds;
                        if (targetMs > nowMs)
                        {
                            Thread.Sleep((int)(targetMs - nowMs));
                        }

                        bool capturedWin = false;
                        if (targetHwnd != IntPtr.Zero && IsWindow(targetHwnd))
                        {
                            WINRECT wr;
                            if (GetWindowRect(targetHwnd, out wr))
                            {
                                int ww = Math.Max(16, wr.Right - wr.Left);
                                int wh = Math.Max(16, wr.Bottom - wr.Top);
                                x = wr.Left;
                                y = wr.Top;
                                if (Math.Abs(ww - width) <= 4 && Math.Abs(wh - height) <= 4)
                                {
                                    IntPtr hdc = g.GetHdc();
                                    try
                                    {
                                        capturedWin = PrintWindow(targetHwnd, hdc, 2);
                                    }
                                    finally
                                    {
                                        g.ReleaseHdc(hdc);
                                    }
                                }
                            }
                        }
                        if (!capturedWin)
                        {
                            g.CopyFromScreen(x, y, 0, 0, new Size(width, height), CopyPixelOperation.SourceCopy);
                        }
                        var bmpData = bmp.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.ReadOnly, PixelFormat.Format32bppRgb);
                        try
                        {
                            IMFMediaBuffer mb;
                            Marshal.ThrowExceptionForHR(MFCreateMemoryBuffer(bufBytes, out mb));
                            IntPtr pDest;
                            uint maxL, curL;
                            Marshal.ThrowExceptionForHR(mb.Lock(out pDest, out maxL, out curL));
                            try
                            {
                                int rowBytes = width * 4;
                                // Flip vertically: RGB32 input in SinkWriter expects bottom-up DIB unless stride negative
                                for (int row = 0; row < height; row++)
                                {
                                    IntPtr srcRow = IntPtr.Add(bmpData.Scan0, (height - 1 - row) * bmpData.Stride);
                                    IntPtr dstRow = IntPtr.Add(pDest, row * rowBytes);
                                    CopyMemory(dstRow, srcRow, rowBytes);
                                }
                            }
                            finally
                            {
                                mb.Unlock();
                            }
                            mb.SetCurrentLength(bufBytes);

                            IMFSample sample;
                            Marshal.ThrowExceptionForHR(MFCreateSample(out sample));
                            sample.AddBuffer(mb);
                            sample.SetSampleTime(frameNum * frameDur);
                            sample.SetSampleDuration(frameDur);
                            lock (writerLock)
                            {
                                Marshal.ThrowExceptionForHR(writer.WriteSample(streamIndex, sample));
                            }

                            Marshal.ReleaseComObject(sample);
                            Marshal.ReleaseComObject(mb);
                        }
                        finally
                        {
                            bmp.UnlockBits(bmpData);
                        }
                        frameNum++;
                    }
                }

                stop = true;
                if (audioThread != null) audioThread.Join(1200);
                lock (writerLock)
                {
                    Marshal.ThrowExceptionForHR(writer.Finalize());
                }
                Console.WriteLine("SAVED:" + outPath);
                return 0;
            }
            finally
            {
                try { if (sysClient != null) sysClient.Stop(); } catch { }
                try { if (micClient != null) micClient.Stop(); } catch { }
                if (writer != null) Marshal.ReleaseComObject(writer);
                MFShutdown();
            }
        }
    }
    #endregion
}
