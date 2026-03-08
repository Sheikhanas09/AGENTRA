"use client";

import { useState, useEffect } from "react";
import {
  FaUser,
  FaEnvelope,
  FaBuilding,
  FaLock,
  FaUpload,
  FaTimes,
} from "react-icons/fa";

/* ================= CEO SETTINGS COMPONENT ================= */
export default function Settings({ initialData }) {
  const [ceoData, setCeoData] = useState({
    name: "",
    email: "",
    companyName: "",
    password: "",
  });

  const [policyFile, setPolicyFile] = useState(null);
  const [showPolicyUploaded, setShowPolicyUploaded] = useState(false);

  useEffect(() => {
    if (initialData) {
      setCeoData(initialData);
      setShowPolicyUploaded(initialData.policyFile ? true : false);
    }
  }, [initialData]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setCeoData({ ...ceoData, [name]: value });
  };

  const handlePolicyUpload = (e) => {
    const file = e.target.files[0];
    if (file) {
      setPolicyFile(file);
      setShowPolicyUploaded(true);
    }
  };

  const handleSave = () => {
    alert("Settings saved successfully!");
    console.log("CEO Data:", ceoData);
    console.log("Policy File:", policyFile);
  };

  return (
    <div className="max-w-4xl mx-auto mt-6 p-6 bg-black/50 backdrop-blur-md border border-[#05DC7F]/30 rounded-2xl shadow-[0_0_20px_rgba(5,220,127,0.25)]">
      <h2 className="text-white text-2xl font-bold text-center mb-2">
        CEO Settings
      </h2>
      <p className="text-gray-400 text-sm text-center mb-6">
        Manage your account info and company policy
      </p>

      {/* ================= GRID FORM ================= */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Full Name */}
        <div className="flex items-center gap-2 border-b border-[#05DC7F]/20 pb-2">
          <FaUser className="text-[#05DC7F] w-5 h-5" />
          <input
            type="text"
            name="name"
            value={ceoData.name}
            onChange={handleChange}
            placeholder="Full Name"
            className="w-full bg-transparent text-white placeholder-gray-400 outline-none"
          />
        </div>

        {/* Email */}
        <div className="flex items-center gap-2 border-b border-[#05DC7F]/20 pb-2">
          <FaEnvelope className="text-[#05DC7F] w-5 h-5" />
          <input
            type="email"
            name="email"
            value={ceoData.email}
            onChange={handleChange}
            placeholder="Email"
            className="w-full bg-transparent text-white placeholder-gray-400 outline-none"
          />
        </div>

        {/* Company Name */}
        <div className="flex items-center gap-2 border-b border-[#05DC7F]/20 pb-2">
          <FaBuilding className="text-[#05DC7F] w-5 h-5" />
          <input
            type="text"
            name="companyName"
            value={ceoData.companyName}
            onChange={handleChange}
            placeholder="Company Name"
            className="w-full bg-transparent text-white placeholder-gray-400 outline-none"
          />
        </div>

        {/* Password */}
        <div className="flex items-center gap-2 border-b border-[#05DC7F]/20 pb-2">
          <FaLock className="text-[#05DC7F] w-5 h-5" />
          <input
            type="password"
            name="password"
            value={ceoData.password}
            onChange={handleChange}
            placeholder="Password"
            className="w-full bg-transparent text-white placeholder-gray-400 outline-none"
          />
        </div>
      </div>

      {/* ================= POLICY UPLOAD ================= */}
      <div className="mt-6">
        <label className="text-gray-400 text-sm mb-1 block">
          Upload Company Policy
        </label>

        {showPolicyUploaded ? (
          <div className="flex items-center justify-between gap-4 px-3 py-2 rounded border border-[#05DC7F]/20 text-white">
            <span className="truncate">
              {policyFile ? policyFile.name : "Policy uploaded"}
            </span>
            <button
              onClick={() => {
                setPolicyFile(null);
                setShowPolicyUploaded(false);
              }}
              className="text-red-500 hover:text-red-400 transition"
            >
              <FaTimes />
            </button>
          </div>
        ) : (
          <label className="flex items-center gap-2 px-3 py-2 rounded border border-[#05DC7F]/30 text-[#05DC7F] cursor-pointer hover:bg-[#05DC7F]/10 transition">
            <FaUpload />
            <span>Choose Policy File</span>
            <input
              type="file"
              accept=".pdf,.doc,.docx"
              onChange={handlePolicyUpload}
              className="hidden"
            />
          </label>
        )}
      </div>

      {/* ================= SAVE BUTTON ================= */}
      <div className="flex justify-center mt-6">
        <button
          onClick={handleSave}
          className="flex items-center gap-2 px-6 py-2.5 bg-[#05DC7F] text-black font-semibold rounded-xl hover:bg-[#04c56f] transition"
        >
          <span>Save Changes</span>
          <FaUpload className="text-black" />
        </button>
      </div>
    </div>
  );
}
