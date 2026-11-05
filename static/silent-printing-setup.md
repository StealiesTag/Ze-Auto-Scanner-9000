# One-time silent printing setup for teachers

Do this once on the Windows computer connected to the answer-sheet printer. Paparazzi runs in a browser and cannot change Windows or browser settings itself.

## 1. Set up the printer

1. Open **Settings → Bluetooth & devices → Printers & scanners**.
2. Select the printer that will feed the answer sheets and choose **Set as default**. If Windows manages the default printer automatically, turn that option off first.
3. Open the printer's printing preferences. Set paper to **A4** and turn **two-sided / duplex** printing off.

## 2. Configure silent printing

Choose one browser setup.

### Chrome (recommended)

1. Close every Chrome window.
2. Find Chrome in the Windows Start menu, right-click it, then choose **Open file location**.
3. In the folder that opens, right-click the Chrome shortcut and choose **Properties**.
4. On the **Shortcut** tab, click the end of the **Target** field. Add a space and `--kiosk-printing` after the closing quote. Keep the existing path. For example:

   ```text
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk-printing
   ```

   The exact Chrome path can differ. Edit the shortcut Windows opened for you; don't replace its path with the example if yours is different.
5. Choose **Apply**, then **OK**. Open Paparazzi using this edited shortcut.

### Microsoft Edge

Edge silent printing is controlled by a Windows policy and needs Edge policy templates and administrator access. Ask your IT administrator to enable **Enable Silent Printing** at **Computer Configuration → Administrative Templates → Microsoft Edge**, then restart Edge. This policy is supported in Edge 144 and later. See [Microsoft's policy instructions](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-browser-policies/silentprintingenabled).

## 3. Start scanning

1. Open Paparazzi in the configured browser and select the assessment.
2. Check **Silent printing is configured on this computer**, then choose **Start one-page scanning**.
3. The printer should feed one blank page without showing a print dialog. Six seconds after each print is initiated, Paparazzi captures the sheet and then requests the next blank page.

If a print dialog appears, cancel it, return to Paparazzi, and choose **Stop** immediately. Close the browser and check the setup above before trying again. The checkbox remembers your confirmation in this browser, but the page cannot verify the browser policy itself.
