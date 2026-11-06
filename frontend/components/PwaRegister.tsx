"use client";
import { useEffect } from "react";
import { registerServiceWorker } from "../lib/pwa";

export default function PwaRegister() {
  useEffect(() => {
    registerServiceWorker();
  }, []);
  return null;
}
