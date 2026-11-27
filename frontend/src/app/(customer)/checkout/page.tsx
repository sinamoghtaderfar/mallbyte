import { BannerSlot } from "@/features/content/components/storefront-banners";
import { SiteShell } from "@/components/layout/site-shell";
import { ProtectedRoute } from "@/features/auth/components/protected-route";
import { CheckoutForm } from "@/features/orders/components/checkout-form";

export default function CheckoutPage() {
  return (
    <SiteShell promotions>
      <main className="px-4 py-12">
        <div className="mx-auto w-full max-w-6xl">
          <ProtectedRoute>
            <BannerSlot placement="checkout" className="mb-5" />
            <CheckoutForm />
          </ProtectedRoute>
        </div>
      </main>
    </SiteShell>
  );
}
