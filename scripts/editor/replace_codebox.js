// Reviewed current editor SDK: range delete + onSelection().insertImage.
// The attachment ID and px-to-twips conversion match cached WebApiHandler.
async function replaceWecomCodebox(options) {
  const editor = window.pad.editor;
  const {start, end, contentId, imageUrl, width, height} = options;
  if (!Number.isInteger(start) || end !== start + 1 || !contentId ||
      !imageUrl.startsWith('https://') || !(width > 0 && height > 0)) {
    throw new Error('Invalid explicit replacement options');
  }
  if (!editor.clipboardManager.context.isEditable()) throw new Error('Document is not editable');
  const manager = window.App.changesetManager;
  const adapter = window.App.collabCenterAdapter;
  if (!adapter.isConnected || manager.hasPauseCollab || manager.composeCommands.length ||
      manager.composeTimer !== null || adapter.getLocalUserChanges().length) {
    throw new Error('Editor has existing pending changes');
  }
  // Verify the image is usable before deleting the old object.
  await new Promise((resolve, reject) => {
    const image = new Image();
    const timer = setTimeout(() => reject(new Error('Replacement image load timed out')), 8000);
    image.onload = () => { clearTimeout(timer); image.naturalWidth > 0 ? resolve() : reject(new Error('Empty image')); };
    image.onerror = () => { clearTimeout(timer); reject(new Error('Replacement image cannot load')); };
    image.src = imageUrl;
  });
  const drawing = editor.state.getDrawingStream().getDrawingInfoFromGcp(start);
  if (!drawing || drawing.range.begin() !== start || drawing.range.end() !== end ||
      editor.service.getDocument().readText(start, 1).charCodeAt(0) !== 8 ||
      drawing.property.drawing.graphic.graphicData.wsp.txbx.contentId !== contentId) {
    throw new Error('Target Code TextBox differs from the saved snapshot');
  }
  const selection = editor.service.getSelection();
  selection.setRange(start, end);
  if (editor.selection.gcpRange.begin() !== start || editor.selection.gcpRange.end() !== end ||
      !editor.state.getRestrictEdit().checkEditableByRange(editor.selection.gcpRange)) {
    throw new Error('Target selection cannot be edited');
  }
  const selected = editor.onSelection();
  if (typeof selected.insertImage !== 'function') throw new Error('Expected image SDK is missing');
  selection.delete();
  const remaining = editor.state.getDrawingStream().getDrawingInfoFromGcp(start);
  if (remaining) {
    const graphic = remaining.property.drawing.graphic.graphicData;
    if (graphic.wsp && graphic.wsp.txbx.contentId === contentId) {
      throw new Error('Code TextBox deletion did not take effect; do not replay');
    }
  }
  selection.setRange(start, start);
  const attachmentId = Math.random().toString(16).substr(2, 8);
  editor.onSelection().insertImage(attachmentId, 15 * width, 15 * height, imageUrl);
  for (let attempt = 0; attempt < 20; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 500));
    const current = editor.state.getDrawingStream().getDrawingInfoFromGcp(start);
    const pic = current && current.property.drawing.graphic.graphicData.pic;
    if (attempt >= 1 && pic && adapter.isConnected && !manager.hasPauseCollab &&
        manager.composeCommands.length === 0 && manager.composeTimer === null &&
        adapter.getLocalUserChanges().length === 0) {
      return {replaced: true, start, end: start + 1, codebox: contentId,
              imagePresent: true, version: adapter.getVersion(), quiescent: true};
    }
  }
  throw new Error('Replacement has not been confirmed; inspect before any repeat');
}
