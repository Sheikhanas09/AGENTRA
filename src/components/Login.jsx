import { useState } from "react";
import bgGlow from "../images/bg.png";
import logo from "../images/logo.png";
import SplashScreen from "./SplashScreen";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showSplash, setShowSplash] = useState(true);

  if (showSplash) {
    return <SplashScreen onFinish={() => setShowSplash(false)} />;
  }

  return (
    <div className="relative min-h-screen w-full bg-[#1F1F1F] overflow-hidden">
      {/* ===== LEFT BOTTOM GLOW ===== */}
      <div
        className="
          absolute pointer-events-none
          left-0 bottom-0
          w-[600px] h-[600px]
          sm:w-[700px] sm:h-[700px]
          lg:w-[800px] lg:h-[800px]
        "
        style={{
          backgroundImage: `url(${bgGlow})`,
          backgroundRepeat: "no-repeat",
          backgroundSize: "1100px",
          transform: "translate(-28%, 40%) rotate(80.48deg)",
          borderRadius: "50%",
          opacity: 1.7,
        }}
      />

      {/* ===== LOGO ===== */}
      <div
        className="
          absolute z-20
          top-4 left-1/2 -translate-x-1/2
          md:left-4 md:translate-x-0
        "
      >
        <img
          src={logo}
          alt="Agentra Logo"
          className="w-24 sm:w-28 md:w-32 lg:w-42"
        />
      </div>

      {/* ===== PAGE CENTER ===== */}
      <div className="relative z-10 min-h-screen flex items-center justify-center px-4 sm:px-6">
        {/* ===== LOGIN CARD ===== */}
        <div
          className="
          w-full max-w-[520px]
          rounded-[56px] sm:rounded-[76px]
          border border-white/20
          bg-transparent
          backdrop-blur-[41px]
          shadow-[0_20px_60px_rgba(0,0,0,0.6)]
          px-6 sm:px-10
          py-10 sm:py-12
        "
        >
          <h1 className="text-white text-[32px] sm:text-[44px] font-bold text-center mb-8">
            Welcome <br /> Back!
          </h1>
          {/* Email */}
          <div className="flex items-center h-[54px] sm:h-[58px] border-2 border-[#DBE3E6] rounded-[12px] mb-5 hover:border-[#05DC7F]">
            <div className="w-[12px] h-full bg-[#05DC7F] rounded-[7px]" />
            <input
              type="email"
              placeholder="Email Address"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="flex-1 px-4 bg-transparent text-white focus:outline-none"
            />
          </div>
          {/* Password */}
          <div className="flex items-center h-[54px] sm:h-[58px] border-2 border-[#DBE3E6] rounded-[12px] mb-7 hover:border-[#05DC7F]">
            <div className="w-[12px] h-full bg-[#05DC7F] rounded-[7px]" />
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="flex-1 px-4 bg-transparent text-white focus:outline-none"
            />
          </div>
          {/* ===== Remember + Forgot ===== */}{" "}
          <div className="flex flex-col sm:flex-row sm:justify-between gap-4 text-sm text-white/70 mb-8">
            {" "}
            <label className="flex items-center gap-2 cursor-pointer">
              {" "}
              <input type="checkbox" className="accent-emerald-400" /> Remember
              me{" "}
            </label>{" "}
            <button className="hover:text-[#05DC7F] transition-colors">
              {" "}
              Forgot Password?{" "}
            </button>{" "}
          </div>
          <button
            className="
            w-full sm:w-[120px]
            h-[44px]
            mx-auto block
            border border-[#05DC7F]
            text-[#05DC7F]
            rounded-[14px]
            hover:bg-[#05DC7F]
            hover:text-black
            transition
          "
          >
            LOGIN
          </button>
        </div>
      </div>

      {/* FOOTER */}
      <div className="absolute bottom-3 right-4 text-white/70 text-xs sm:text-sm text-right">
        AI-powered HR & Agent <br />
        Management System
      </div>
    </div>
  );
}
