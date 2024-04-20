FROM node:18-alpine AS base
WORKDIR /app

# Dependencies stage
FROM base AS deps
RUN apk add --no-cache libc6-compat
COPY package*.json ./
RUN npm install

# Builder stage
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# Runner stage
FROM base AS runner
RUN apk add --no-cache curl
ENV NODE_ENV production
ENV NEXT_TELEMETRY_DISABLED 1

# Add user for running the application
RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# Set up directories and permissions
RUN mkdir -p .next/cache
RUN chown nextjs:nodejs .next/cache

# Copy built assets from the builder stage
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./next
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./static

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]