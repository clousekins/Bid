; ====== CONFIG ======
profileName := "Brian Gou"
baseDir     := "D:\my resume"
promptDir   := baseDir . "\resume_prompt"
appliedDir  := baseDir . "\applied"

; Build resume prompt file path from profileName
mdFile := promptDir . "\" . profileName . ".txt"
; ====================


!1::
    ; --- Save original clipboard and clear it for reliable copy ---
    ClipSaved := ClipboardAll
    Clipboard := ""

    ; --- Copy selected text (Job Description) ---
    Send, ^c
    ClipWait, 2
    if ErrorLevel {
        MsgBox, 48, Clipboard Error, Failed to copy text to clipboard.
        Clipboard := ClipSaved
        return
    }
    JD := Clipboard

    ; --- Switch to editor / new window (Win+1). Use WinActivate if needed ---
    Send, #1
    Sleep, 300

    ; --- Trigger new file / open ---
    Send, ^+o
    Sleep, 500

    ; --- Paste Job Description ---
    Clipboard := JD
    ClipWait, 2
    if ErrorLevel {
        MsgBox, 48, Clipboard Error, Failed to set clipboard to JD for paste.
        Clipboard := ClipSaved
        return
    }
    Send, ^v
    Sleep, 400
    Send, +{Enter 2}
    Sleep, 200

    ; --- Paste Resume Prompt Markdown ---
    if !FileExist(mdFile) {
        MsgBox, 48, File Error, Missing file:`n%mdFile%
        Clipboard := ClipSaved
        return
    }
    FileRead, resumePrompt, %mdFile%
    if ErrorLevel {
        MsgBox, 48, File Error, Could not read file:`n%mdFile%
        Clipboard := ClipSaved
        return
    }
    Clipboard := resumePrompt
    ClipWait, 2
    if ErrorLevel {
        MsgBox, 48, Clipboard Error, Failed to set clipboard to resume prompt.
        Clipboard := ClipSaved
        return
    }
    Send, ^v
    Sleep, 200
    Send, {Enter}
    Sleep, 120

    ; --- Restore original clipboard ---
    Clipboard := ClipSaved
    VarSetCapacity(ClipSaved, 0)
return


; --- Show Clipboard + Convert to PDF using Chrome ---
^1::
{
    ClipWait, 2
    if (ErrorLevel)
    {
        MsgBox, 48, Error, Clipboard is empty or not ready!
        return
    }

    FormatTime, currentDateTime,, yyyy-MM-dd HH:mm:ss
    text := Clipboard

    ; --- Extract <title>...</title> from the HTML in clipboard ---
    titleText := ""
    if RegExMatch(text, "is)<title>\s*(.*?)\s*</title>", m)
        titleText := m1
    else
        titleText := "Untitled"

    ; ///////////////////////////////////////////////////////////////////////////////////////
companyName := ""
existingFolderName := ""
existingAgeText := "??months:??Days:??hours:??Minutes"

parts := StrSplit(titleText, "_")
if (parts.Length() >= 3)
    companyName := parts[3]
else
    companyName := "UnknownCompany"

; Prevent empty/unknown company matching everything
if (companyName = "" || companyName = "UnknownCompany")
{
    MsgBox, 48, Company Name Error, Could not extract company name from title:`n%titleText%
    return
}

baseProfileDir := appliedDir . "\" . profileName
IfNotExist, %baseProfileDir%
    FileCreateDir, %baseProfileDir%

foundExisting := false

Loop, Files, % baseProfileDir . "\*", D   ; directories only, no recursion
{
    if InStr(A_LoopFileName, companyName, false)
    {
        foundExisting := true
        existingFolderName := A_LoopFileName

        ; Parse timestamp prefix: yyyy_MM_dd_HH_mm_
        if RegExMatch(existingFolderName, "O)^(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_", m)
        {
            existingStamp := m.Value(1) . m.Value(2) . m.Value(3) . m.Value(4) . m.Value(5) . "00"  ; YYYYMMDDHHMISS

            diffSec := A_Now
            EnvSub, diffSec, %existingStamp%, Seconds
            if (diffSec < 0)
                diffSec := -diffSec

            diffMinTotal := Floor(diffSec / 60)
            diffHrTotal  := Floor(diffMinTotal / 60)
            diffDayTotal := Floor(diffHrTotal / 24)

            diffMo  := Floor(diffDayTotal / 30)     ; approx months (30 days)
            remDays := Mod(diffDayTotal, 30)
            remHrs  := Mod(diffHrTotal, 24)
            remMins := Mod(diffMinTotal, 60)

            existingAgeText := Format("{:02}months:{:02}Days:{:02}hours:{:02}Minutes"
                                    , diffMo, remDays, remHrs, remMins)
        }

        break
    }
}

if (foundExisting)
{
    ; Build message using expression concatenation (NO %vars% here)
    msg := companyName " already exists.`n"
         . "Before: " existingAgeText "`n"
         . "Foldername: " existingFolderName "`n`n"
         . "Continue anyway?"

    ; 52 = 4 (Yes/No) + 48 (Warning icon)
    MsgBox, 52, Company Exists, %msg%
    IfMsgBox, No
        return
}

    ; //////////////////////////////////////////////////////////////////////////////////////

    FormatTime, currentStamp,, yyyy_MM_dd_HH_mm
    titleTextWithTime := currentStamp . "_" . titleText

    ; --- Create folder for the company if it doesn't exist ---
    companyFolder := appliedDir . "\" . profileName . "\" . titleTextWithTime
    IfNotExist, %companyFolder%
        FileCreateDir, %companyFolder%

    ; --- Append Job Description to a file in the company's folder ---
    logFile := companyFolder . "\JD.txt"
    logText := "`n# " . currentDateTime . " : JD`n`n" . JD . "`n`n"
    FileAppend, %logText%, %logFile%, UTF-8

    ; --- Prepare file paths (use profileName instead of hard-coded text) ---
    HtmlFile := companyFolder . "\" . profileName . ".html"
    PdfFile  := companyFolder . "\" . profileName . ".pdf"

    ; --- Write HTML file ---
    HtmlContent := text
    FileDelete, %HtmlFile%
    FileAppend, %HtmlContent%, %HtmlFile%, UTF-8

    ; --- Convert HTML → PDF ---
    
    chromePath := ProgramFiles . "\Google\Chrome\Application\chrome.exe"

 
    RunWait, "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --headless=new --no-pdf-header-footer --disable-gpu --run-all-compositor-stages-before-draw --font-render-hinting=none --disable-font-subpixel-positioning --force-device-scale-factor=1 --print-to-pdf="%PdfFile%" "%HtmlFile%", , Hide

    if FileExist(PdfFile)
        MsgBox, 64, Success, PDF saved at:`n%PdfFile%
    else
        MsgBox, 48, Failed, Could not generate PDF.
}
return