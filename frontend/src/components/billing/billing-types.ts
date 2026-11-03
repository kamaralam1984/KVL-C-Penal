export interface PlanVersion { version: number; price_monthly_paise: number; price_yearly_paise: number; limits: Record<string, number>; features: Record<string, boolean>; trial_days: number; hsn_sac: string; gst_rate_bps: number }
export interface Plan { id: string; code: string; name: string; description: string | null; active: boolean; current_version: PlanVersion; versions?: PlanVersion[]; subscribers?: number }
export interface Addon { id: string; code: string; name: string; price_monthly_paise: number; price_yearly_paise: number; limits: Record<string, number>; active: boolean }
export interface Sub {
  id: string; tenant_id: string; tenant_name?: string; status: string; interval: string; plan: { code: string; name: string; version: number }; terms: PlanVersion;
  addons: { code: string; qty: number; unit_price_paise: number }[]; current_period_start: string | null; current_period_end: string | null; trial_ends_at: string | null; grace_ends_at: string | null;
  cancel_at_period_end: boolean; pending_change: { effective_at: string } | null; mrr_paise: number;
}
export interface Invoice {
  id: string; number: string; tenant_id: string; purpose: string; status: string; subtotal_paise: number; discount_paise: number; taxable_paise: number; cgst_paise: number; sgst_paise: number; igst_paise: number;
  total_paise: number; refunded_paise: number; due_at: string | null; issued_at: string; paid_at: string | null; dunning_attempts: number; last_failed_at: string | null; coupon_code: string | null;
  lines?: { description: string; hsn_sac: string; qty: number; amount_paise: number }[]; place_of_supply?: string | null; seller?: Record<string, string | null>; buyer?: Record<string, string | null>;
  payments?: Payment[]; credit_notes?: { id: string; number: string; amount_paise: number }[];
}
export interface Payment { id: string; invoice_id: string; provider: string; status: string; amount_paise: number; refunded_paise: number; failure_reason: string | null; created_at: string; captured_at: string | null; awaiting_confirmation: boolean }
export interface PaymentBlock { status: "paid" | "not_configured" | "order_created"; message?: string; payment_id?: string; order_id?: string; key_id?: string; amount_paise?: number }
