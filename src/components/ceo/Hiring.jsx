"use client";

import { useState } from "react";
import CompletedTab from "./CompletedTab";
import { FaTimes } from "react-icons/fa";

export default function Hiring({ jobPosts }) {
  const [showPopup, setShowPopup] = useState(true);
  const [selectedJob, setSelectedJob] = useState(null);

  const handleSelect = (job) => {
    setSelectedJob(job);
    setShowPopup(false);
  };

  return (
    <div className="relative">
      {/* FIRST POPUP */}
      {showPopup && !selectedJob && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-md bg-black/90 border border-[#05DC7F]/40 rounded-2xl p-6 shadow-[0_0_25px_rgba(5,220,127,0.4)] relative">
            <button
              onClick={() => setShowPopup(false)}
              className="absolute top-4 right-4 text-gray-400 hover:text-white"
            >
              <FaTimes />
            </button>

            <h2 className="text-white text-lg font-bold mb-4 text-center">
              Select Job Post
            </h2>

            <select
              onChange={(e) => {
                const job = jobPosts.find(
                  (j) => j.id === Number(e.target.value),
                );
                handleSelect(job);
              }}
              className="w-full px-4 py-2 rounded-lg bg-black border border-[#05DC7F]/30 text-white focus:outline-none focus:border-[#05DC7F]"
              defaultValue=""
            >
              <option value="" disabled>
                Choose a job...
              </option>

              {jobPosts.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.title} | {job.postedAt}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* TOP RIGHT SELECT */}
      {selectedJob && (
        <div className="flex justify-end mb-6">
          <div className="flex items-center gap-3 bg-black/40 border border-[#05DC7F]/30 rounded-xl px-4 py-2 shadow-[0_0_10px_rgba(5,220,127,0.2)]">
            <span className="text-gray-400 text-sm">Hiring For:</span>

            <select
              value={selectedJob.id}
              onChange={(e) => {
                const job = jobPosts.find(
                  (j) => j.id === Number(e.target.value),
                );
                setSelectedJob(job);
              }}
              className="bg-black text-[#05DC7F] font-semibold outline-none cursor-pointer"
            >
              {jobPosts.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.title} | {job.postedAt}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {selectedJob && <CompletedTab />}
    </div>
  );
}
