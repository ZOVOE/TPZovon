# 🔑 Multiple Stripe Keys Guide

## ✅ Yes! The bot supports unlimited Stripe keys with automatic rotation

## 📊 How It Works

The Card Monitor Bot automatically:
1. ✅ Loads ALL keys from `keys.json` (all groups)
2. ✅ Randomly selects a key for each card check
3. ✅ Distributes load across all available keys
4. ✅ Continues working if individual keys fail

## 🎯 Benefits of Multiple Keys

### 1. **Rate Limit Distribution**
- Each Stripe key has rate limits
- Multiple keys = multiply your capacity
- Example: 5 keys = 5x throughput

### 2. **Higher Reliability**
- If one key expires/fails, others keep working
- Automatic failover - no manual intervention
- Reduces downtime

### 3. **Better Performance**
- Parallel processing across multiple accounts
- No bottleneck from single key limits
- Faster overall processing

### 4. **Geographic Distribution**
- Use keys from different Stripe accounts
- Better global coverage
- Reduced latency

## 📝 Configuration

### Basic Setup (Single Group)

```json
{
  "default": [
    {"sk": "sk_live_key1", "pk": "pk_live_key1"},
    {"sk": "sk_live_key2", "pk": "pk_live_key2"},
    {"sk": "sk_live_key3", "pk": "pk_live_key3"}
  ]
}
```

### Advanced Setup (Multiple Groups)

```json
{
  "primary": [
    {"sk": "sk_live_primary1", "pk": "pk_live_primary1"},
    {"sk": "sk_live_primary2", "pk": "pk_live_primary2"},
    {"sk": "sk_live_primary3", "pk": "pk_live_primary3"}
  ],
  "backup": [
    {"sk": "sk_live_backup1", "pk": "pk_live_backup1"},
    {"sk": "sk_live_backup2", "pk": "pk_live_backup2"}
  ],
  "testing": [
    {"sk": "sk_live_testing1", "pk": "pk_live_testing1"}
  ],
  "high_limit": [
    {"sk": "sk_live_premium1", "pk": "pk_live_premium1"}
  ]
}
```

**Bot uses ALL keys** from all groups!

## 🔄 How Key Selection Works

```python
# Pseudo-code of the bot's logic
keys = load_all_keys_from_all_groups()  # Loads all keys
random_key = random.choice(keys)        # Random selection
check_card(card, random_key)            # Uses selected key
```

### Example with 6 keys:
- Check 1: Uses key #3 (random)
- Check 2: Uses key #1 (random)
- Check 3: Uses key #5 (random)
- Check 4: Uses key #3 (random - can repeat)
- ... and so on

## 📈 Capacity Planning

| Keys | Approximate Capacity | Use Case |
|------|---------------------|----------|
| 1    | Basic               | Testing  |
| 3-5  | Moderate            | Small scale |
| 5-10 | High                | Medium scale |
| 10+  | Very High           | Large scale |

## 💡 Best Practices

### 1. **Organize by Purpose**
```json
{
  "production": [...],  // Main working keys
  "backup": [...],      // Fallback keys
  "testing": [...]      // Test keys
}
```

### 2. **Use Multiple Stripe Accounts**
- Don't put all keys from one account
- Spread across multiple Stripe accounts
- Better risk distribution

### 3. **Monitor Individual Keys**
- Check Stripe dashboard regularly
- Track which keys are hitting limits
- Replace expired/blocked keys promptly

### 4. **Rotate Keys Regularly**
```bash
# Update keys.json with new keys
nano keys.json

# Restart bot to reload
pkill -f card_monitor_bot.py
./run_bot.sh
```

### 5. **Start Small, Scale Up**
- Begin with 2-3 keys
- Monitor performance
- Add more keys as needed

## 🔍 Verification

To verify your keys are loaded:

```bash
# Check if keys.json is valid JSON
python3 -m json.tool keys.json

# Start bot and check logs
./run_bot.sh

# You should see in logs:
# "Stripe keys loaded successfully" or similar
```

## 🎯 Example Scenarios

### Scenario 1: High Volume
```json
{
  "pool1": [
    {"sk": "sk_live_1", "pk": "pk_live_1"},
    {"sk": "sk_live_2", "pk": "pk_live_2"},
    {"sk": "sk_live_3", "pk": "pk_live_3"}
  ],
  "pool2": [
    {"sk": "sk_live_4", "pk": "pk_live_4"},
    {"sk": "sk_live_5", "pk": "pk_live_5"}
  ]
}
```
**Result:** Bot uses all 5 keys randomly

### Scenario 2: Backup Strategy
```json
{
  "main": [
    {"sk": "sk_live_main1", "pk": "pk_live_main1"},
    {"sk": "sk_live_main2", "pk": "pk_live_main2"}
  ],
  "emergency": [
    {"sk": "sk_live_emergency", "pk": "pk_live_emergency"}
  ]
}
```
**Result:** Usually uses main keys, emergency key available if needed

### Scenario 3: Testing + Production
```json
{
  "production": [
    {"sk": "sk_live_prod1", "pk": "pk_live_prod1"},
    {"sk": "sk_live_prod2", "pk": "pk_live_prod2"}
  ],
  "testing": [
    {"sk": "sk_test_test1", "pk": "pk_test_test1"}
  ]
}
```
⚠️ **Note:** Bot loads all keys including test mode! Use only live keys for production.

## 🚨 Troubleshooting

### Issue: Only one key being used
**Cause:** keys.json has only one key
**Fix:** Add more keys to the JSON file

### Issue: Some keys not working
**Cause:** Individual keys might be expired/invalid
**Fix:** The bot will skip bad keys and use others

### Issue: All checks failing
**Cause:** All keys are invalid
**Fix:** 
```bash
# Verify keys in Stripe dashboard
# Update keys.json with valid keys
nano keys.json
```

### Issue: Want to prioritize certain keys
**Note:** Current implementation uses random selection
**Workaround:** Add high-priority keys multiple times
```json
{
  "keys": [
    {"sk": "sk_live_priority", "pk": "pk_live_priority"},
    {"sk": "sk_live_priority", "pk": "pk_live_priority"},  // Added twice
    {"sk": "sk_live_normal", "pk": "pk_live_normal"}
  ]
}
```
Priority key has 2x chance of selection

## 📊 Monitoring

Track key usage in Stripe:
1. Go to Stripe Dashboard
2. View Logs → Filter by API key
3. Monitor request counts per key
4. Check for errors/rate limits

## 🔐 Security

1. **Never commit keys.json** (already in .gitignore)
2. **Use different keys for different purposes**
3. **Rotate keys monthly**
4. **Monitor for unauthorized usage**
5. **Use Stripe's webhook alerts**

## ✅ Summary

| Feature | Status |
|---------|--------|
| Multiple Keys | ✅ Supported |
| Automatic Rotation | ✅ Yes (random) |
| All Groups Loaded | ✅ Yes |
| Failover | ✅ Automatic |
| Unlimited Keys | ✅ Yes |
| Hot Reload | ❌ Requires restart |

## 🎉 Ready to Use!

Just add your keys to `keys.json` and the bot will automatically use them all!

```bash
# 1. Add your keys
nano keys.json

# 2. Restart bot
./run_bot.sh

# 3. Check it's working
tail -f bot.log
```

**The more keys you add, the better the performance!** 🚀
