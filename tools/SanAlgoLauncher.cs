using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Threading;

namespace SanAlgo
{
    class Program
    {
        private static bool IsPortOccupied(int port)
        {
            try
            {
                using (var client = new TcpClient("127.0.0.1", port))
                {
                    return true;
                }
            }
            catch
            {
                return false;
            }
        }

        static void Main(string[] args)
        {
            Console.Title = "SanAlgo — VAR_117 Multi-Asset ETF Trading Engine";

            bool isNewInstance;
            using (Mutex mutex = new Mutex(true, "Local\\SanAlgo_Engine_SingleInstance_Mutex", out isNewInstance))
            {
                if (!isNewInstance || IsPortOccupied(8080))
                {
                    Console.ForegroundColor = ConsoleColor.Yellow;
                    Console.WriteLine("================================================================");
                    Console.WriteLine("   SanAlgo — VAR_117 Multi-Asset ETF Momentum Rotation Engine   ");
                    Console.WriteLine("================================================================");
                    Console.WriteLine("[!] SanAlgo is ALREADY RUNNING in another window.");
                    Console.WriteLine("[+] Opening your web dashboard: http://127.0.0.1:8080");
                    Console.WriteLine("================================================================");
                    Console.ResetColor();

                    try
                    {
                        Process.Start(new ProcessStartInfo("http://127.0.0.1:8080") { UseShellExecute = true });
                    }
                    catch { }

                    Thread.Sleep(2500);
                    return;
                }

                Console.ForegroundColor = ConsoleColor.Green;
                Console.WriteLine("================================================================");
                Console.WriteLine("   SanAlgo — VAR_117 Multi-Asset ETF Momentum Rotation Engine   ");
                Console.WriteLine("================================================================");
                Console.ResetColor();

                // Locate project root
                string appDir = AppDomain.CurrentDomain.BaseDirectory;
                if (!File.Exists(Path.Combine(appDir, "pyproject.toml")))
                {
                    appDir = @"C:\Users\Admin\Documents\code\Gbot";
                }

                Directory.SetCurrentDirectory(appDir);
                Console.WriteLine("[+] Working Directory: " + appDir);

                // Determine launcher executable
                string uvPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".local\bin\uv.exe");
                string cmdExe = "uv";
                string cmdArgs = "run python -m uvicorn api.main:app --host 127.0.0.1 --port 8080";

                if (File.Exists(uvPath))
                {
                    cmdExe = uvPath;
                }
                else
                {
                    string venvPython = Path.Combine(appDir, @".venv\Scripts\python.exe");
                    if (File.Exists(venvPython))
                    {
                        cmdExe = venvPython;
                        cmdArgs = "-m uvicorn api.main:app --host 127.0.0.1 --port 8080";
                    }
                }

                Console.WriteLine("[+] Command: " + cmdExe + " " + cmdArgs);
                Console.WriteLine("[+] Web Dashboard: http://127.0.0.1:8080");
                Console.WriteLine("[*] Starting engine... Browser will open automatically in 2.5 seconds.");
                Console.WriteLine("[*] Press Ctrl+C or close this window to safely stop the engine.");
                Console.WriteLine("----------------------------------------------------------------");

                // Open browser in background thread after 2.5 seconds
                new Thread(() =>
                {
                    Thread.Sleep(2500);
                    try
                    {
                        Process.Start(new ProcessStartInfo("http://127.0.0.1:8080") { UseShellExecute = true });
                    }
                    catch { }
                }).Start();

                // Launch engine process
                ProcessStartInfo psi = new ProcessStartInfo
                {
                    FileName = cmdExe,
                    Arguments = cmdArgs,
                    WorkingDirectory = appDir,
                    UseShellExecute = false,
                };

                try
                {
                    using (Process proc = Process.Start(psi))
                    {
                        proc.WaitForExit();
                    }
                }
                catch (Exception ex)
                {
                    Console.ForegroundColor = ConsoleColor.Red;
                    Console.WriteLine("[-] Failed to start engine: " + ex.Message);
                    Console.ResetColor();
                    Console.WriteLine("Press any key to exit...");
                    Console.ReadKey();
                }
            }
        }
    }
}
