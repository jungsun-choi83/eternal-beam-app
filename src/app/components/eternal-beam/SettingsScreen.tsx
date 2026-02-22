import { ArrowRight, User, Globe, Bell, Shield, FileText, HelpCircle, LogOut, Trash2, ChevronRight, Mail, MessageSquare, CreditCard } from 'lucide-react';
import { useLanguage } from '../../contexts/LanguageContext';
import { useState } from 'react';

interface SettingsScreenProps {
  onNavigate?: (screen: string) => void;
}

export function SettingsScreen({ onNavigate }: SettingsScreenProps) {
  const { t, language } = useLanguage();
  const [pushEnabled, setPushEnabled] = useState(true);
  const [emailEnabled, setEmailEnabled] = useState(false);

  const planLabel = language === 'ko' ? '베이직 플랜' : language === 'en' ? 'Basic Plan' : '基础版';
  const langLabel = language === 'ko' ? '한국어' : language === 'en' ? 'English' : '中文';

  const settingsSections = [
    {
      title: t('settings.account'),
      items: [
        {
          icon: User,
          label: t('settings.profile'),
          value: language === 'ko' ? '김지수' : language === 'en' ? 'Sarah Johnson' : '李娜',
          action: () => {}
        },
        {
          icon: Globe,
          label: t('settings.language'),
          value: langLabel,
          action: () => {}
        },
        {
          icon: CreditCard,
          label: t('payment.title'),
          value: planLabel,
          action: () => onNavigate?.('checkout')
        }
      ]
    },
    {
      title: t('settings.notifications'),
      items: [
        {
          icon: Bell,
          label: t('settings.push'),
          toggle: true,
          value: pushEnabled,
          action: () => setPushEnabled(!pushEnabled)
        },
        {
          icon: Mail,
          label: t('settings.email'),
          toggle: true,
          value: emailEnabled,
          action: () => setEmailEnabled(!emailEnabled)
        }
      ]
    },
    {
      title: t('settings.privacy'),
      items: [
        {
          icon: Shield,
          label: t('settings.privacy'),
          action: () => {}
        },
        {
          icon: FileText,
          label: t('settings.terms'),
          action: () => {}
        },
        {
          icon: FileText,
          label: t('settings.policy'),
          action: () => {}
        }
      ]
    },
    {
      title: t('settings.support'),
      items: [
        {
          icon: HelpCircle,
          label: t('settings.faq'),
          action: () => {}
        },
        {
          icon: MessageSquare,
          label: t('settings.contact'),
          action: () => {}
        }
      ]
    }
  ];

  return (
    <div className="h-full w-full relative overflow-y-auto bg-transparent">
      {/* Header */}
      <div className="glass sticky top-0 z-10 px-6 py-4 border-0 rounded-b-2xl">
        <div className="flex items-center justify-between mb-2">
          <h1 className="font-headline text-2xl font-bold" style={{ color: '#2d3748' }}>
          {t('settings.title')}
        </h1>
          <button
            onClick={() => onNavigate?.('home')}
            className="glass-strong w-10 h-10 rounded-full flex items-center justify-center shrink-0"
          >
            <ArrowRight className="w-5 h-5 text-[#7C6B9B]" />
          </button>
        </div>
      </div>

      {/* Profile Card */}
      <div className="px-6 py-6">
        <div
          className="rounded-2xl p-6 flex items-center gap-4"
          style={{
            background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
            boxShadow: '0 8px 24px rgba(102, 126, 234, 0.3)'
          }}
        >
          <div
            className="w-16 h-16 rounded-full flex items-center justify-center text-2xl"
            style={{
              background: 'rgba(255, 255, 255, 0.2)',
              border: '3px solid rgba(255, 255, 255, 0.3)'
            }}
          >
            👤
          </div>
          <div className="flex-1">
            <h3 className="text-xl font-bold text-white mb-1" style={{
              fontFamily: 'var(--font-headline)'
            }}>
              {language === 'ko' ? '김지수' : language === 'en' ? 'Sarah Johnson' : '李娜'}
            </h3>
            <p className="text-sm text-white/80">
              jisu.kim@example.com
            </p>
          </div>
          <button
            className="w-10 h-10 rounded-full flex items-center justify-center"
            style={{
              background: 'rgba(255, 255, 255, 0.2)'
            }}
          >
            <ChevronRight className="w-5 h-5 text-white" />
          </button>
        </div>
      </div>

      {/* Settings Sections */}
      <div className="px-6 pb-24 space-y-6">
        {settingsSections.map((section, sectionIndex) => (
          <div key={sectionIndex}>
            <h3 className="text-xs font-bold mb-3 px-2" style={{
              color: '#a0aec0',
              letterSpacing: '0.05em'
            }}>
              {section.title}
            </h3>

              <div className="glass rounded-2xl overflow-hidden">
              {section.items.map((item, itemIndex) => {
                const IconComponent = item.icon;
                return (
                  <div
                    key={itemIndex}
                    role="button"
                    tabIndex={0}
                    onClick={item.action}
                    onKeyDown={(e) => e.key === 'Enter' && item.action?.()}
                    className="w-full px-4 py-4 flex items-center gap-4 transition-all cursor-pointer"
                    style={{
                      borderBottom: itemIndex < section.items.length - 1
                        ? '1px solid rgba(0, 0, 0, 0.05)'
                        : 'none'
                    }}
                  >
                    <div
                      className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
                      style={{
                        background: 'rgba(102, 126, 234, 0.1)'
                      }}
                    >
                      <IconComponent className="w-5 h-5" style={{ color: '#667eea' }} />
                    </div>

                    <div className="flex-1 min-w-0 text-left">
                      <h4 className="font-semibold" style={{ color: '#2d3748' }}>
                        {item.label}
                      </h4>
                      {item.value !== undefined && !item.toggle && (
                        <p className="text-xs mt-1" style={{ color: '#718096' }}>
                          {item.value}
                        </p>
                      )}
                    </div>

                    {item.toggle !== undefined ? (
                      <div
                        role="switch"
                        tabIndex={0}
                        aria-checked={item.value}
                        onClick={(e) => {
                          e.stopPropagation();
                          item.action?.();
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            e.stopPropagation();
                            item.action?.();
                          }
                        }}
                        className="relative w-12 h-6 rounded-full transition-all cursor-pointer shrink-0"
                        style={{
                          background: item.value
                            ? 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
                            : 'rgba(160, 174, 192, 0.3)'
                        }}
                      >
                        <div
                          className="absolute w-5 h-5 rounded-full bg-white top-0.5 transition-all"
                          style={{
                            left: item.value ? 'calc(100% - 22px)' : '2px',
                            boxShadow: '0 2px 4px rgba(0, 0, 0, 0.2)'
                          }}
                        />
                      </div>
                    ) : (
                      <ChevronRight className="w-5 h-5 shrink-0" style={{ color: '#a0aec0' }} />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}

        {/* Version */}
        <div className="text-center py-4">
          <p className="text-xs mb-1" style={{ color: '#a0aec0' }}>
            {t('settings.version')}
          </p>
          <p className="text-sm font-bold" style={{ color: '#718096' }}>
            Eternal Beam v1.0.0
          </p>
          <p className="text-xs mt-1" style={{ color: '#a0aec0' }}>
            © 2026 AIEVER. All rights reserved.
          </p>
        </div>

        {/* Danger Zone */}
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => onNavigate?.('home')}
            className="w-full py-4 rounded-2xl flex items-center justify-center gap-2 font-semibold transition-all"
            style={{
              background: 'rgba(239, 68, 68, 0.1)',
              color: '#ef4444',
              border: '1px solid rgba(239, 68, 68, 0.2)'
            }}
          >
            <LogOut className="w-5 h-5" />
            {t('settings.logout')}
          </button>

          <button
            type="button"
            className="w-full py-4 rounded-2xl flex items-center justify-center gap-2 font-semibold transition-all"
            style={{
              background: 'rgba(220, 38, 38, 0.1)',
              color: '#dc2626',
              border: '1px solid rgba(220, 38, 38, 0.2)'
            }}
          >
            <Trash2 className="w-5 h-5" />
            {t('settings.delete')}
          </button>
        </div>
      </div>
    </div>
  );
}
