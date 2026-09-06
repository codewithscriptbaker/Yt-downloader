import type { Metadata } from "next";
import { AuthForm } from "@/components/AuthForm";

export const metadata: Metadata = {
  title: "Log in — MediaPort",
  description: "Log in to MediaPort to sync your download history.",
};

export default function LoginPage() {
  return <AuthForm mode="login" />;
}
