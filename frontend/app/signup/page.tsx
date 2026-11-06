"use client";
import { useRouter } from "next/navigation";
import { SignupForm } from "../../components/Auth";
import { AuthShell } from "../../components/ui";

/** Separate sign-up page (parents only; children get their accounts from the parent). */
export default function Signup() {
  const router = useRouter();
  return (
    <AuthShell subtitle="학부모 계정을 만들고 아이의 한자 공부를 시작해요">
      <SignupForm onSignedUp={() => router.push("/")} />
    </AuthShell>
  );
}
