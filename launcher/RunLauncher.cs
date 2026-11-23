using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class RunLauncher
{
    [STAThread]
    private static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "run.ps1");

        if (!File.Exists(script))
        {
            MessageBox.Show(
                "Could not find run.ps1 in the same folder.",
                "BCTO90 Controller",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.System),
                    "WindowsPowerShell\\v1.0\\powershell.exe"),
                Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            Process.Start(startInfo);
        }
        catch (Exception exception)
        {
            MessageBox.Show(
                "Could not start the program.\n\n" + exception.Message,
                "BCTO90 Controller",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }
}
