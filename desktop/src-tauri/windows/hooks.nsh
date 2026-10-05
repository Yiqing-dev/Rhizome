; SPDX-License-Identifier: Apache-2.0
; Installer hooks. The library (database, exports, PDFs) lives in the data directory, never in the
; installation folder, so uninstalling or upgrading never touches it.
;
; Before files are replaced, a running Rhizome (the window, its backend, and the MCP server that
; Claude Desktop started) must stop. The user is asked first: Cancel aborts the upgrade and leaves
; everything running; OK closes them (Claude Desktop reconnects its tools after a restart).

!macro RHIZOME_STOP_RUNNING
  nsExec::ExecToStack 'cmd /C tasklist /FI "IMAGENAME eq rhz.exe" /NH | find /I "rhz.exe"'
  Pop $0
  Pop $1
  StrCmp $0 "0" 0 +3
    MessageBox MB_OKCANCEL|MB_ICONQUESTION "Rhizome 正在运行（窗口、后台服务或 Claude Desktop 的连接）。按“确定”关闭它们并继续安装；按“取消”放弃安装。$\r$\n$\r$\nRhizome is running (the window, its backend or Claude Desktop's connection). OK closes them and continues; Cancel aborts the installation." IDOK +2
    Abort
  nsExec::Exec 'taskkill /F /IM Rhizome.exe'
  nsExec::Exec 'taskkill /F /IM rhz.exe'
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro RHIZOME_STOP_RUNNING
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec 'taskkill /F /IM Rhizome.exe'
  nsExec::Exec 'taskkill /F /IM rhz.exe'
!macroend
