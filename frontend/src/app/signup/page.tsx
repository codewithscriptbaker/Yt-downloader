import type { Metadata } from "next";
import { AuthForm } from "@/components/AuthForm";

export const metadata: Metadata = {
  title: "Sign up — MediaPort",
  description: "Create a MediaPort account to keep download history across devices.",
};

export default function SignupPage() {
  return <AuthForm mode="signup" />;
}
