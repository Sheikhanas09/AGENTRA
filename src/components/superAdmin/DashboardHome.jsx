"use client";

import { FaBriefcase, FaCheck, FaTimes } from "react-icons/fa";

export default function DashboardHome() {
  return (
    <div className="text-white/70">
      <h2 className="text-2xl font-semibold mb-4">Overview</h2>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
        <div className="flex justify-between items-center p-5 rounded-2xl backdrop-blur-sm border border-[#05DC7F]/25 shadow-[0_0_10px_rgba(5,220,127,0.25)] hover:border-[#05DC7F]/45 hover:shadow-[0_0_18px_rgba(5,220,127,0.45)] transition-all duration-300">
          <div>
            <p className="text-gray-400 text-sm">Total Companies</p>
            <h3 className="text-3xl font-bold text-white mt-2">25</h3>
          </div>
          <div className="w-12 h-12 rounded-full flex items-center justify-center bg-[#05DC7F]/15 border border-[#05DC7F]/40 text-[#05DC7F] text-xl">
            <FaBriefcase />
          </div>
        </div>
        <div className="flex justify-between items-center p-5 rounded-2xl backdrop-blur-sm border border-[#05DC7F]/25 shadow-[0_0_10px_rgba(5,220,127,0.25)] hover:border-[#05DC7F]/45 hover:shadow-[0_0_18px_rgba(5,220,127,0.45)] transition-all duration-300">
          <div>
            <p className="text-gray-400 text-sm">Active Companies</p>
            <h3 className="text-3xl font-bold text-[#05DC7F] mt-2">18</h3>
          </div>
          <div className="w-12 h-12 rounded-full flex items-center justify-center bg-[#05DC7F]/15 border border-[#05DC7F]/40 text-[#05DC7F] text-xl">
            <FaCheck />
          </div>
        </div>
        <div className="flex justify-between items-center p-5 rounded-2xl backdrop-blur-sm border border-[#05DC7F]/25 shadow-[0_0_10px_rgba(5,220,127,0.25)] hover:border-[#05DC7F]/45 hover:shadow-[0_0_18px_rgba(5,220,127,0.45)] transition-all duration-300">
          <div>
            <p className="text-gray-400 text-sm">Pending Requests</p>
            <h3 className="text-3xl font-bold text-yellow-400 mt-2">7</h3>
          </div>
          <div className="w-12 h-12 rounded-full flex items-center justify-center bg-[#05DC7F]/15 border border-[#05DC7F]/40 text-[#05DC7F] text-xl">
            <FaTimes />
          </div>
        </div>
      </div>
    </div>
  );
}
