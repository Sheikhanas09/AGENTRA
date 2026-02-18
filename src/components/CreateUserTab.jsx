"use client";

import { useState } from "react";
import {
  FaUserPlus,
  FaKey,
  FaSync,
  FaEnvelope,
  FaPhone,
  FaBuilding,
} from "react-icons/fa";

/* ================= PASSWORD GENERATOR ================= */
function generatePassword(length = 10) {
  const chars =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@#$!";
  let password = "";
  for (let i = 0; i < length; i++) {
    password += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return password;
}

/* ================= EMPLOYEE ID GENERATOR ================= */
function generateEmployeeID() {
  return "EMP-" + Math.floor(1000 + Math.random() * 9000);
}

/* ================= MAIN COMPONENT ================= */
export default function CreateUser() {
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    phone: "",
    department: "Frontend Developer",
    role: "Employee",
    joiningDate: "",
    status: "Active",
    password: "",
  });

  const [autoPassword, setAutoPassword] = useState(true);
  const [createdUsers, setCreatedUsers] = useState([]);
  const [success, setSuccess] = useState(false);

  /* ===== HANDLE INPUT CHANGE ===== */
  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  /* ===== HANDLE CREATE USER ===== */
  const handleCreateUser = (e) => {
    e.preventDefault();

    if (!formData.name || !formData.email)
      return alert("Name and Email are required");

    const finalPassword = autoPassword ? generatePassword() : formData.password;

    if (!finalPassword) return alert("Password required");

    const newUser = {
      ...formData,
      id: Date.now(),
      employeeID: generateEmployeeID(),
      password: finalPassword,
    };

    setCreatedUsers([newUser, ...createdUsers]);

    setSuccess(true);
    setTimeout(() => setSuccess(false), 3000);

    setFormData({
      name: "",
      email: "",
      phone: "",
      department: "Frontend Developer",
      role: "Employee",
      joiningDate: "",
      status: "Active",
      password: "",
    });
  };

  return (
    <div className="flex flex-col gap-6 md:gap-10 w-full px-4 md:px-0">
      {/* ===== FORM SECTION ===== */}
      <div
        className="p-6 md:p-8 rounded-2xl bg-black/40 backdrop-blur-md
        border border-[#05DC7F]/30
        shadow-[0_0_25px_rgba(5,220,127,0.15)] w-full"
      >
        <h2 className="text-2xl md:text-3xl font-bold text-white mb-6 flex items-center gap-3">
          <FaUserPlus className="text-[#05DC7F]" />
          Create Employee Account
        </h2>

        {success && (
          <div className="mb-4 p-3 rounded-lg bg-[#05DC7F]/20 border border-[#05DC7F] text-[#05DC7F] text-sm">
            ✅ Employee created successfully
          </div>
        )}

        <form
          onSubmit={handleCreateUser}
          className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6"
        >
          {/* FULL NAME */}
          <div className="w-full">
            <label className="text-gray-400 text-sm">Full Name *</label>
            <input
              type="text"
              name="name"
              value={formData.name}
              onChange={handleChange}
              className="input-style"
              placeholder="Enter full name"
            />
          </div>

          {/* EMAIL */}
          <div className="w-full">
            <label className="text-gray-400 text-sm flex items-center gap-2">
              <FaEnvelope className="text-[#05DC7F]" />
              Email *
            </label>
            <input
              type="email"
              name="email"
              value={formData.email}
              onChange={handleChange}
              className="input-style"
              placeholder="Enter email"
            />
          </div>

          {/* PHONE */}
          <div className="w-full">
            <label className="text-gray-400 text-sm flex items-center gap-2">
              <FaPhone className="text-[#05DC7F]" />
              Phone
            </label>
            <input
              type="text"
              name="phone"
              value={formData.phone}
              onChange={handleChange}
              className="input-style"
              placeholder="Enter phone number"
            />
          </div>

          {/* DEPARTMENT */}
          <div className="w-full">
            <label className="text-gray-400 text-sm flex items-center gap-2">
              <FaBuilding className="text-[#05DC7F]" />
              Department
            </label>
            <select
              name="department"
              value={formData.department}
              onChange={handleChange}
              className="input-style"
            >
              <option>Frontend Developer</option>
              <option>Backend Developer</option>
              <option>UI/UX Designer</option>
              <option>Human Resources</option>
              <option>Finance</option>
              <option>Marketing</option>
            </select>
          </div>

          {/* ROLE */}
          <div className="w-full">
            <label className="text-gray-400 text-sm">Role</label>
            <select
              name="role"
              value={formData.role}
              onChange={handleChange}
              className="input-style"
            >
              <option>Employee</option>
              <option>Manager</option>
              <option>Admin</option>
            </select>
          </div>

          {/* JOINING DATE */}
          <div className="w-full">
            <label className="text-gray-400 text-sm">Joining Date</label>
            <input
              type="date"
              name="joiningDate"
              value={formData.joiningDate}
              onChange={handleChange}
              className="input-style"
            />
          </div>

          {/* STATUS */}
          <div className="w-full">
            <label className="text-gray-400 text-sm">Account Status</label>
            <select
              name="status"
              value={formData.status}
              onChange={handleChange}
              className="input-style"
            >
              <option>Active</option>
              <option>Inactive</option>
            </select>
          </div>

          {/* PASSWORD SECTION */}
          <div className="md:col-span-2 flex flex-col gap-4 border-t border-gray-700 pt-6">
            <div className="flex flex-col md:flex-row items-start md:items-center gap-3">
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={autoPassword}
                  onChange={() => setAutoPassword(!autoPassword)}
                />
                <label className="text-gray-400 text-sm">
                  Auto Generate Password
                </label>
              </div>

              {autoPassword && (
                <button
                  type="button"
                  onClick={() =>
                    setFormData({
                      ...formData,
                      password: generatePassword(),
                    })
                  }
                  className="ml-auto flex items-center gap-2 text-sm text-[#05DC7F]"
                >
                  <FaSync /> Regenerate
                </button>
              )}
            </div>

            {!autoPassword && (
              <input
                type="text"
                name="password"
                value={formData.password}
                onChange={handleChange}
                className="input-style"
                placeholder="Enter password"
              />
            )}
          </div>

          {/* SUBMIT */}
          <div className="md:col-span-2">
            <button
              type="submit"
              className="w-full py-3 rounded-xl bg-[#05DC7F]
              text-black font-semibold hover:bg-[#04c56f] transition"
            >
              Create Employee
            </button>
          </div>
        </form>
      </div>

      {/* ===== CREATED USERS ===== */}
      {createdUsers.length > 0 && (
        <div className="flex flex-col gap-4 w-full">
          <h3 className="text-white text-xl md:text-2xl font-semibold">
            Recently Created Users
          </h3>

          {createdUsers.map((user) => (
            <div
              key={user.id}
              className="p-5 rounded-xl bg-black/40 border border-[#05DC7F]/25 w-full"
            >
              <div className="flex flex-col md:flex-row justify-between flex-wrap gap-4">
                <div>
                  <p className="text-white font-semibold">{user.name}</p>
                  <p className="text-gray-400 text-sm md:text-base">
                    {user.department} • {user.role}
                  </p>
                  <p className="text-gray-500 text-xs md:text-sm">
                    ID: {user.employeeID}
                  </p>
                </div>

                <div className="text-sm md:text-base text-[#05DC7F] font-mono break-words">
                  {user.password}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ===== COMMON INPUT STYLE ===== */}
      <style jsx>{`
        .input-style {
          width: 100%;
          margin-top: 6px;
          padding: 12px;
          border-radius: 12px;
          background: rgba(0, 0, 0, 0.6);
          border: 1px solid #374151;
          color: white;
          outline: none;
          transition: all 0.3s ease;
        }

        .input-style:focus {
          border-color: #05dc7f;
          box-shadow: 0 0 12px rgba(5, 220, 127, 0.4);
        }

        /* ===== MODERN SELECT STYLE ===== */
        select.input-style {
          appearance: none;
          -webkit-appearance: none;
          -moz-appearance: none;

          background-image: url("data:image/svg+xml;utf8,<svg fill='%2305DC7F' height='20' viewBox='0 0 20 20' width='20' xmlns='http://www.w3.org/2000/svg'><path d='M5 7l5 5 5-5'/></svg>");
          background-repeat: no-repeat;
          background-position: right 12px center;
          background-size: 16px;
          cursor: pointer;
        }

        select.input-style:hover {
          border-color: #05dc7f;
        }

        select.input-style option {
          background-color: #0f0f0f;
          color: white;
        }
      `}</style>
    </div>
  );
}
