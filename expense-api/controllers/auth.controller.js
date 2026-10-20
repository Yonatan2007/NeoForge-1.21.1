import bcrypt from "bcrypt";
import jwt from "jsonwebtoken";
import User from "../models/user.model.js";

const SALT_ROUNDS = 10;
const INVALID_CREDENTIALS = "פרטי התחברות שגויים";

function createToken(userId) {
  return jwt.sign({ userId }, process.env.JWT_SECRET, { expiresIn: process.env.JWT_EXPIRES_IN || "1d" });
}

export async function register(req, res) {
  const { name, email, password } = req.validated;

  const exists = await User.exists({ email });
  if (exists) {
    return res.status(409).json({ message: "Email already exists" });
  }

  const passwordHash = await bcrypt.hash(password, SALT_ROUNDS);
  const user = await User.create({ name, email, passwordHash });

  res.status(201).json({ user });
}

export async function login(req, res) {
  const { email, password } = req.validated;

  const user = await User.findOne({ email });
  // same message whether the email or the password is wrong
  const ok = user && (await bcrypt.compare(password, user.passwordHash));
  if (!ok) {
    return res.status(401).json({ message: INVALID_CREDENTIALS });
  }

  res.status(200).json({ token: createToken(user._id.toString()), user });
}
