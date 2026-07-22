export const tokenDialogMethods = {
  requireToken({ title, message, confirmText, action }) {
    this.tokenDialog = { open: true, title, message, confirmText, token: "", action };
    this.$nextTick(() => {
      const input = document.querySelector(".token-dialog input");
      if (input) input.focus();
    });
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
    const token = this.tokenDialog.token.trim();
    if (!token) return this.showNotice("bad", "请输入管理令牌");
    const action = this.tokenDialog.action;
    if (!action) return;
    this.busy = true;
    try {
      await action(token);
      this.resetTokenDialog();
    } catch (error) {
      this.showNotice("bad", error.message);
    } finally {
      this.busy = false;
    }
  },
};
