"use client";
import { useRouter } from "next/navigation";
import { SignupForm } from "../../components/Auth";

/** Separate sign-up page (parents only; children get their accounts from the parent). */
export default function Signup() {
  const router = useRouter();
  return (
    <main>
      <h1>한자 튜터</h1>
      <SignupForm onSignedUp={() => router.push("/")} />
    </main>
  );
}
