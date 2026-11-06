import { AuthShell, buttonClass, Card } from "../../../components/ui";

export default function BillingFail() {
  return (
    <AuthShell subtitle="구독 결제">
      <Card className="space-y-4 text-center">
        <p className="text-lg font-semibold text-danger-700 dark:text-danger-300">카드 등록에 실패했어요. 다시 시도해 주세요.</p>
        <a href="/" className={buttonClass("primary", "md", "w-full no-underline hover:no-underline dark:text-brand-950")}>돌아가기</a>
      </Card>
    </AuthShell>
  );
}
