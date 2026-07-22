export const tokenDialogMethods = {
  requireToken({ title, message, confirmText, action }) {
    this.tokenDialog = { open: true, title, message, confirmText, requireToken: false, token: "", action };
  },
  closeTokenDialog() {
    if (this.busy) return;
    this.resetTokenDialog();
  },
  resetTokenDialog() {
    this.tokenDialog.open = false;
    this.tokenDialog.token = "";
    this.tokenDialog.action = null;
  },
  async confirmTokenDialog() {
    const token = this.tokenDialog.requireToken ? this.tokenDialog.token.trim() : "session";
    if (!token) return this.showNotice("bad", "请输入管理令牌");
    const action = this.tokenDialog.action;
    if (!action) return;
    this.busy = true;
    try {
      await action("");
      this.resetTokenDialog();
    } catch (error) {
      this.showNotice("bad", error.message);
    } finally {
      this.busy = false;
    }
  },
};
