"use client";

import { useState } from "react";

export default function CreateJobTab() {
  const [formData, setFormData] = useState({
    title: "",
    experience: "",
    timing: "",
    location: "",
    salary: "",
    extra: "",
  });

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData({ ...formData, [name]: value });
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    console.log("Job Data:", formData);
    // Here you can send the data to backend or generate job post
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-4 sm:gap-6 text-gray-300"
    >
      <h3 className="text-white text-xl sm:text-2xl font-semibold mb-4">
        Create a New Job
      </h3>

      {/* ===== Job Title ===== */}
      <div className="flex flex-col w-full">
        <label className="text-gray-400 mb-1 sm:mb-2 text-sm sm:text-base">
          Job Title *
        </label>
        <input
          type="text"
          name="title"
          value={formData.title}
          onChange={handleChange}
          placeholder="e.g., Software Engineer"
          required
          className="w-full bg-black/30 border border-[#05DC7F]/20 rounded-xl px-3 sm:px-4 py-2 sm:py-2.5 text-white text-sm sm:text-base focus:outline-none focus:border-[#05DC7F] focus:ring-1 focus:ring-[#05DC7F] transition"
        />
      </div>

      {/* ===== Experience ===== */}
      <div className="flex flex-col w-full">
        <label className="text-gray-400 mb-1 sm:mb-2 text-sm sm:text-base">
          Experience Required *
        </label>
        <input
          type="text"
          name="experience"
          value={formData.experience}
          onChange={handleChange}
          placeholder="e.g., 3-5 years"
          required
          className="w-full bg-black/30 border border-[#05DC7F]/20 rounded-xl px-3 sm:px-4 py-2 sm:py-2.5 text-white text-sm sm:text-base focus:outline-none focus:border-[#05DC7F] focus:ring-1 focus:ring-[#05DC7F] transition"
        />
      </div>

      {/* ===== Timing ===== */}
      <div className="flex flex-col w-full">
        <label className="text-gray-400 mb-1 sm:mb-2 text-sm sm:text-base">
          Job Timing *
        </label>
        <input
          type="text"
          name="timing"
          value={formData.timing}
          onChange={handleChange}
          placeholder="e.g., Full-time / Part-time"
          required
          className="w-full bg-black/30 border border-[#05DC7F]/20 rounded-xl px-3 sm:px-4 py-2 sm:py-2.5 text-white text-sm sm:text-base focus:outline-none focus:border-[#05DC7F] focus:ring-1 focus:ring-[#05DC7F] transition"
        />
      </div>

      {/* ===== Location ===== */}
      <div className="flex flex-col w-full">
        <label className="text-gray-400 mb-1 sm:mb-2 text-sm sm:text-base">
          Location *
        </label>
        <input
          type="text"
          name="location"
          value={formData.location}
          onChange={handleChange}
          placeholder="e.g., Remote / New York"
          required
          className="w-full bg-black/30 border border-[#05DC7F]/20 rounded-xl px-3 sm:px-4 py-2 sm:py-2.5 text-white text-sm sm:text-base focus:outline-none focus:border-[#05DC7F] focus:ring-1 focus:ring-[#05DC7F] transition"
        />
      </div>

      {/* ===== Salary ===== */}
      <div className="flex flex-col w-full">
        <label className="text-gray-400 mb-1 sm:mb-2 text-sm sm:text-base">
          Salary *
        </label>
        <input
          type="text"
          name="salary"
          value={formData.salary}
          onChange={handleChange}
          placeholder="e.g., $50,000 - $70,000"
          required
          className="w-full bg-black/30 border border-[#05DC7F]/20 rounded-xl px-3 sm:px-4 py-2 sm:py-2.5 text-white text-sm sm:text-base focus:outline-none focus:border-[#05DC7F] focus:ring-1 focus:ring-[#05DC7F] transition"
        />
      </div>

      {/* ===== Optional Extra Info ===== */}
      <div className="flex flex-col w-full">
        <label className="text-gray-400 mb-1 sm:mb-2 text-sm sm:text-base">
          Additional Information (Optional)
        </label>
        <textarea
          name="extra"
          value={formData.extra}
          onChange={handleChange}
          placeholder="Any extra notes or requirements..."
          rows={4}
          className="w-full bg-black/30 border border-[#05DC7F]/20 rounded-xl px-3 sm:px-4 py-2 sm:py-2.5 text-white text-sm sm:text-base focus:outline-none focus:border-[#05DC7F] focus:ring-1 focus:ring-[#05DC7F] transition resize-none"
        />
      </div>

      {/* ===== Generate Button ===== */}
      <button
        type="submit"
        className="self-start bg-[#05DC7F] hover:bg-[#04c56f] text-black font-semibold px-5 sm:px-6 py-2 sm:py-2.5 rounded-xl shadow-[0_0_12px_rgba(5,220,127,0.35)] hover:shadow-[0_0_20px_rgba(5,220,127,0.55)] transition text-sm sm:text-base"
      >
        Generate
      </button>
    </form>
  );
}
