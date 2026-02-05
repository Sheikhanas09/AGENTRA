import { useEffect, useState } from "react";
import logo from "../images/logo.png";
import CircuitBackground from "./CircuitBackground";

export default function SplashScreen({ onFinish }) {
  const [fade, setFade] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setFade(true); // Start fade-out
      setTimeout(() => {
        onFinish(); // Trigger next screen after fade
      }, 1000); // Duration must match CSS fade animation
    }, 4500); // Splash screen visible duration

    return () => clearTimeout(timer);
  }, [onFinish]);

  return (
    <div
      className={`fixed inset-0 z-50 overflow-hidden transition-opacity duration-1000 ${
        fade ? "opacity-0" : "opacity-100"
      }`}
    >
      {/* 🔥 NEON CIRCUIT BACKGROUND */}
      <CircuitBackground />

      {/* 🌈 NEON OVERLAY GRADIENT */}
      <div
        className="
          absolute inset-0
          bg-gradient-to-br
          from-[#1F1F1F]/90
          via-[#1F1F1F]/80
          to-[#1F1F1F]/90
        "
      />

      {/* 🌟 LOGO FOREGROUND */}
      <div className="relative z-10 flex items-center justify-center h-full px-4">
        <div
          className="
            flex items-center justify-center
            w-40 h-40
            sm:w-52 sm:h-52
            md:w-64 md:h-64
            lg:w-72 lg:h-72
            xl:w-80 xl:h-80
            rounded-full
            bg-black/20
            backdrop-blur-xl
            shadow-[0_0_160px_rgba(0,255,170,1)]
            animate-splash
          "
        >
          <img
            src={logo}
            alt="Agentra Logo"
            className="
              w-28
              sm:w-32
              md:w-40
              lg:w-44
              xl:w-52
              object-contain
              drop-shadow-[0_0_55px_rgba(0,0,0,0.4)]
            "
          />
        </div>
      </div>
    </div>
  );
}
