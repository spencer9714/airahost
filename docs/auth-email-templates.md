# AiraHost — Auth Email Templates

> 本文件記錄兩封 Supabase Auth 發送的 email（確認信 + 重設密碼信）的正式文案、HTML 模板，以及如何在 Supabase Dashboard 套用它們。
>
> HTML 原始碼存放於 `supabase/email-templates/`。

---

## 現況說明

這兩封信**由 Supabase Auth 負責發送**，不是在 repo 內自行呼叫 SMTP。
模板必須在 **Supabase Dashboard → Authentication → Email Templates** 手動設定。

- `supabase/email-templates/confirmation.html` → 貼到 **Confirm signup** 模板
- `supabase/email-templates/reset-password.html` → 貼到 **Reset password** 模板

Supabase 模板語法為 Go template 格式，官方支援的變數如下：

| 變數 | 說明 |
|---|---|
| `{{ .ConfirmationURL }}` | 完整的確認 / 重設連結（含 token 與 redirect） |
| `{{ .Email }}` | 使用者的 email |
| `{{ .SiteURL }}` | Supabase Dashboard 設定的 Site URL |
| `{{ .Token }}` | OTP token（本專案不使用） |

---

## 一、確認信（Confirm Signup）

### Subject

```
Confirm your AiraHost account
```

### Preview text（部分 email client 支援）

```
One click to activate your account — it takes less than a minute.
```

### 純文字版本

```
Welcome to AiraHost.

Please confirm your email address to activate your account.

Confirm my account:
{{ .ConfirmationURL }}

This link expires in 24 hours. If you didn't create an AiraHost account, you can safely ignore this email.

—
AiraHost — AI Revenue Advisor for Airbnb Hosts
{{ .SiteURL }}
```

### HTML 版本

HTML 完整原始碼：`supabase/email-templates/confirmation.html`

摘要設計：
- 白底卡片，`#d4450f` accent 色 CTA 按鈕
- 按鈕文案：**Confirm my account →**
- 備援文字連結（若按鈕失效）
- 底部說明：24 小時有效期 + 「若非本人操作可忽略」

---

## 二、重設密碼信（Reset Password）

### Subject

```
Reset your AiraHost password
```

### Preview text

```
We received a request to reset your password. This link is valid for 1 hour.
```

### 純文字版本

```
Hi,

We received a request to reset the password for your AiraHost account.

Click the link below to set a new password:
{{ .ConfirmationURL }}

This link expires in 1 hour.

If you didn't request a password reset, you can safely ignore this email — your password won't change.

—
AiraHost — AI Revenue Advisor for Airbnb Hosts
{{ .SiteURL }}
```

### HTML 版本

HTML 完整原始碼：`supabase/email-templates/reset-password.html`

摘要設計：
- 白底卡片，`#d4450f` accent 色 CTA 按鈕
- 按鈕文案：**Set new password →**
- 備援文字連結
- 橘色安全提醒區塊：1 小時有效期 + 「若非本人操作可忽略」

---

## 三、Supabase Dashboard 設定步驟

1. 進入 **[Supabase Dashboard](https://app.supabase.com)** → 選擇 AiraHost 專案
2. 左側選單：**Authentication** → **Email Templates**
3. 選 **Confirm signup**：
   - 將 `supabase/email-templates/confirmation.html` 的內容貼入 **Body** 欄位
   - Subject 填入：`Confirm your AiraHost account`
4. 選 **Reset password**：
   - 將 `supabase/email-templates/reset-password.html` 的內容貼入 **Body** 欄位
   - Subject 填入：`Reset your AiraHost password`
5. 點 **Save** 儲存

> ⚠️ Supabase 免費方案每小時有 Email 發送頻率限制（預設 3 封/小時）。
> 正式環境建議設定自訂 SMTP（Dashboard → Authentication → Settings → SMTP Settings）。

---

## 四、Auth Flow 技術說明

### 確認信流程（PKCE）

```
signUp({ emailRedirectTo: "${origin}/auth/callback" })
  → Supabase 發送確認信，{{ .ConfirmationURL }} 指向 Supabase verify endpoint
  → 使用者點擊 → Supabase 驗證 → redirect 到 /auth/callback?code=...
  → /auth/callback 呼叫 exchangeCodeForSession(code)
  → 成功後 redirect 到 /dashboard
```

### 重設密碼流程（PKCE）

```
resetPasswordForEmail(email, {
  redirectTo: "${origin}/auth/callback?next=/reset-password"
})
  → Supabase 發送重設信，{{ .ConfirmationURL }} 指向 Supabase verify endpoint
  → 使用者點擊 → Supabase 驗證 → redirect 到 /auth/callback?code=...&next=/reset-password
  → /auth/callback 呼叫 exchangeCodeForSession(code)（建立暫時 session）
  → redirect 到 /reset-password
  → 使用者輸入新密碼 → supabase.auth.updateUser({ password })
  → 成功後 redirect 到 /dashboard
```

### 關鍵檔案

| 檔案 | 用途 |
|---|---|
| `src/app/login/page.tsx` | 登入 + 註冊表單；包含「Forgot your password?」連結 |
| `src/app/forgot-password/page.tsx` | 輸入 email 送出重設請求 |
| `src/app/reset-password/page.tsx` | 輸入新密碼並呼叫 updateUser() |
| `src/app/auth/callback/route.ts` | PKCE code exchange；讀取 `?next=` 決定最終 redirect |

---

## 五、Supabase Site URL 設定確認

確認 Dashboard → **Authentication → URL Configuration** 中：
- **Site URL** 設為 `https://yourdomain.com`（正式環境）
- **Redirect URLs** 中包含 `https://yourdomain.com/auth/callback`

本地開發可額外加入：
- `http://localhost:3000/auth/callback`
