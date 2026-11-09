' llama-shell 启动器
' 启动前依次自检: 解释器 -> tkinter -> (交给 boot_check.py 检查 pip 依赖)
' 缺任何一项都给出可操作提示, 能自动装的就给一键安装, 不再"双击没反应"。
Option Explicit
Dim fso, ws, scriptDir, exe, exePy, cmd, rc, answer, wg

Set fso = CreateObject("Scripting.FileSystemObject")
Set ws  = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = scriptDir

Function FindExe(fileName)
  Dim arr, i, p
  FindExe = ""
  arr = Split(ws.ExpandEnvironmentStrings("%PATH%"), ";")
  For i = 0 To UBound(arr)
    p = Trim(arr(i))
    If Len(p) > 0 Then
      If fso.FileExists(p & "\" & fileName) Then
        FindExe = p & "\" & fileName
        Exit Function
      End If
    End If
  Next
End Function

exe = FindExe("pythonw.exe")
exePy = FindExe("python.exe")

' ── 1. 没有 Python: 给出安装途径(winget 有就装, 没有就开下载页) ──
If exe = "" And exePy = "" Then
  answer = MsgBox("未找到 Python，程序无法启动。" & vbCrLf & vbCrLf & _
                  "是否现在安装 Python 3.12？" & vbCrLf & _
                  "（有 winget 时自动安装；没有则打开官网下载页）", _
                  vbYesNo + vbQuestion, "llama-shell - 缺少 Python")
  If answer = vbYes Then
    wg = FindExe("winget.exe")
    If wg <> "" Then
      rc = ws.Run("""" & wg & """ install -e --id Python.Python.3.12 " _
                  & "--accept-package-agreements --accept-source-agreements", 1, True)
      If rc <> 0 Then
        MsgBox "自动安装未成功，将打开官网下载页。" & vbCrLf & _
               "安装时请务必勾选 " & Chr(34) & "Add python.exe to PATH" & Chr(34) & "。", _
               vbInformation, "llama-shell"
        ws.Run "https://www.python.org/downloads/", 1, False
      End If
    Else
      MsgBox "本机没有 winget，将打开官网下载页。" & vbCrLf & _
             "安装时请务必勾选 " & Chr(34) & "Add python.exe to PATH" & Chr(34) & "。", _
             vbInformation, "llama-shell"
      ws.Run "https://www.python.org/downloads/", 1, False
    End If
  End If
  MsgBox "安装完成后请重新双击本快捷方式。" & vbCrLf & vbCrLf & _
         "其余依赖（ttkbootstrap）会在下次启动时自动检查并一键安装。", _
         vbInformation, "llama-shell"
  WScript.Quit 0
End If

If exe = "" Then exe = exePy

' ── 2. tkinter 是标准库, pip 补不了, 只能重装 Python 时勾选 tcl/tk ──
cmd = """" & exe & """ -c ""import tkinter"""
On Error Resume Next
rc = ws.Run(cmd, 0, True)
If Err.Number <> 0 Then
  rc = -1
  Err.Clear
End If
On Error GoTo 0

If rc <> 0 Then
  MsgBox "Python 缺少 tkinter 组件（图形界面库）。" & vbCrLf & vbCrLf & _
         "这不是 pip 能补的：请重新运行 Python 安装包，选 Modify，" & vbCrLf & _
         "勾选 " & Chr(34) & "tcl/tk and IDLE" & Chr(34) & "。" & vbCrLf & vbCrLf & _
         "解释器: " & exe, 16, "llama-shell - 缺少 tkinter"
  WScript.Quit 1
End If

' ── 3. 其余交给 boot_check.py: 它查 ttkbootstrap, 缺则弹一键安装窗 ──
' 依赖齐全时它自己拉起 main.py 并退出, 不额外耗时
cmd = """" & exe & """ boot_check.py"
ws.Run cmd, 0, False
