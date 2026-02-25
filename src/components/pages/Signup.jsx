import { useState } from "react";
import { useNavigate } from "react-router-dom";
import bgGlow from "../../images/bg.png";
import logo from "../../images/logo.png";

export default function Signup() {
  const navigate = useNavigate();

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  return (
    <div className="relative min-h-screen w-full bg-[#1F1F1F] overflow-hidden flex items-center justify-center px-4">
      {/* ===== LEFT BOTTOM GLOW ===== */}
      <div
        className="absolute pointer-events-none left-0 bottom-0 w-[700px] h-[700px]"
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
      <div className="absolute z-20 top-4 left-1/2 -translate-x-1/2 md:left-4 md:translate-x-0">
        <img src={logo} alt="Agentra Logo" className="w-24 sm:w-28 md:w-32" />
      </div>

      {/* ===== SIGNUP CARD ===== */}
      <div
        className="
        relative z-10
        w-full max-w-[480px]
        rounded-[56px] sm:rounded-[70px]
        border border-white/20
        bg-transparent
        backdrop-blur-[41px]
        shadow-[0_20px_60px_rgba(0,0,0,0.6)]
        px-6 sm:px-8
        py-6 sm:py-8
      "
      >
        <h1 className="text-white text-[30px] sm:text-[38px] font-bold text-center mb-6">
          Create <br /> Account
        </h1>

        {/* Full Name */}
        <div className="flex items-center h-[48px] sm:h-[52px] border-2 border-[#DBE3E6] rounded-[12px] mb-4 hover:border-[#05DC7F]">
          <div className="w-[12px] h-full bg-[#05DC7F] rounded-[7px]" />
          <input
            type="text"
            placeholder="Full Name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            className="flex-1 px-4 bg-transparent text-white focus:outline-none"
          />
        </div>

        {/* Email */}
        <div className="flex items-center h-[48px] sm:h-[52px] border-2 border-[#DBE3E6] rounded-[12px] mb-4 hover:border-[#05DC7F]">
          <div className="w-[12px] h-full bg-[#05DC7F] rounded-[7px]" />
          <input
            type="email"
            placeholder="Email Address"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="flex-1 px-4 bg-transparent text-white focus:outline-none"
          />
        </div>

        {/* Company Name */}
        <div className="flex items-center h-[48px] sm:h-[52px] border-2 border-[#DBE3E6] rounded-[12px] mb-4 hover:border-[#05DC7F]">
          <div className="w-[12px] h-full bg-[#05DC7F] rounded-[7px]" />
          <input
            type="text"
            placeholder="Company Name"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            className="flex-1 px-4 bg-transparent text-white focus:outline-none"
          />
        </div>

        {/* Password */}
        <div className="flex items-center h-[48px] sm:h-[52px] border-2 border-[#DBE3E6] rounded-[12px] mb-4 hover:border-[#05DC7F]">
          <div className="w-[12px] h-full bg-[#05DC7F] rounded-[7px]" />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="flex-1 px-4 bg-transparent text-white focus:outline-none"
          />
        </div>

        {/* Confirm Password */}
        <div className="flex items-center h-[48px] sm:h-[52px] border-2 border-[#DBE3E6] rounded-[12px] mb-5 hover:border-[#05DC7F]">
          <div className="w-[12px] h-full bg-[#05DC7F] rounded-[7px]" />
          <input
            type="password"
            placeholder="Confirm Password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="flex-1 px-4 bg-transparent text-white focus:outline-none"
          />
        </div>

        {/* REGISTER BUTTON */}
        <button
          className="
          w-full sm:w-[140px]
          h-[42px]
          mx-auto block
          border border-[#05DC7F]
          text-[#05DC7F]
          rounded-[14px]
          hover:bg-[#05DC7F]
          hover:text-black
          transition
        "
        >
          REGISTER
        </button>

        {/* LOGIN LINK */}
        <p className="text-center text-white/70 text-sm mt-5">
          Already have an account?{" "}
          <span
            onClick={() => navigate("/")}
            className="text-[#05DC7F] cursor-pointer hover:underline"
          >
            Login
          </span>
        </p>
      </div>

      {/* FOOTER */}
      <div className="absolute bottom-3 right-4 text-white/70 text-xs sm:text-sm text-right">
        AI-powered HR & Agent <br />
        Management System
      </div>
    </div>
  );
}
