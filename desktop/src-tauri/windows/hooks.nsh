; SPDX-License-Identifier: Apache-2.0
; Installer hooks. The library (database, exports, PDFs) lives in the data directory, never in the
; installation folder, so uninstalling or upgrading never touches it.

!macro NSIS_HOOK_PREINSTALL
  ; stop a backend left running by a previous version before its files are replaced (no-op if none)
  nsExec::Exec 'taskkill /F /IM rhz.exe'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec 'taskkill /F /IM rhz.exe'
!macroend
