// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import * as React from 'react';
import { useForm, Controller, FieldValues, UseFormReturn, FieldError, FieldErrors } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z, ZodType } from 'zod';
import { cn } from '@/lib/utils';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import { Label } from '@/components/ui/Label';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card';
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from '@/components/ui/Tooltip';
import { Loader2, Eye, EyeOff, AlertCircle, Info } from 'lucide-react';

export interface JsonSchemaProperty {
  type: 'string' | 'number' | 'boolean' | 'array' | 'object';
  title: string;
  description?: string;
  format?: 'password' | 'email' | 'uri' | 'secret' | 'date' | 'datetime';
  enum?: string[];
  default?: unknown;
  items?: JsonSchemaProperty;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  pattern?: string;
  ui?: {
    widget?: 'text' | 'textarea' | 'select' | 'switch' | 'hidden';
    placeholder?: string;
    helpText?: string;
    options?: { value: string; label: string }[];
  };
}

export interface JsonSchema {
  type: 'object';
  properties: Record<string, JsonSchemaProperty>;
  required?: string[];
}

export interface DynamicFormProps {
  schema: JsonSchema;
  onSubmit: (data: Record<string, unknown>) => Promise<void> | void;
  defaultValues?: Record<string, unknown>;
  submitText?: string;
  cancelText?: string;
  onCancel?: () => void;
  loading?: boolean;
  showCancel?: boolean;
  className?: string;
  layout?: 'vertical' | 'horizontal' | 'grid';
  cols?: 1 | 2 | 3;
}

function jsonSchemaToZod(schema: JsonSchema): z.ZodObject<Record<string, ZodType>> {
  const shape: Record<string, ZodType> = {};

  for (const [key, prop] of Object.entries(schema.properties)) {
    let zodType: ZodType;

    switch (prop.type) {
      case 'string': {
        let stringType: z.ZodString = z.string();
        if (prop.format === 'email') stringType = stringType.email('Invalid email format');
        if (prop.format === 'uri') stringType = stringType.url('Invalid URL format');
        if (prop.minLength) stringType = stringType.min(prop.minLength, `Minimum ${prop.minLength} characters`);
        if (prop.maxLength) stringType = stringType.max(prop.maxLength, `Maximum ${prop.maxLength} characters`);
        if (prop.pattern) stringType = stringType.regex(new RegExp(prop.pattern), 'Invalid format');
        if (prop.enum) {
          zodType = z.enum(prop.enum as [string, ...string[]]);
        } else {
          // `z.string()` của Zod CHẤP NHẬN chuỗi rỗng. Nên một trường bắt
          // buộc để trống vẫn qua được validate và onSubmit vẫn chạy — đúng
          // kiểu "báo lỗi ở giao diện nhưng dữ liệu vẫn đi". Trường bắt buộc
          // phải chặn rỗng thật sự.
          if (schema.required?.includes(key) && !prop.minLength) {
            stringType = stringType.min(1, `${prop.title} là bắt buộc`);
          }
          zodType = stringType;
        }
        break;
      }
      case 'number': {
        let numberType: z.ZodNumber = z.number();
        if (prop.minimum !== undefined) numberType = numberType.min(prop.minimum, `Minimum value is ${prop.minimum}`);
        if (prop.maximum !== undefined) numberType = numberType.max(prop.maximum, `Maximum value is ${prop.maximum}`);
        zodType = numberType;
        break;
      }
      case 'boolean':
        zodType = z.boolean();
        break;
      case 'array':
        zodType = z.array(z.unknown());
        break;
      case 'object':
        if (prop.properties) {
          zodType = jsonSchemaToZod({ type: 'object', properties: prop.properties, required: prop.required });
        } else {
          zodType = z.record(z.unknown());
        }
        break;
      default:
        zodType = z.unknown();
    }

    if (prop.default !== undefined) {
      zodType = zodType.default(prop.default);
    }

    if (!schema.required?.includes(key)) {
      zodType = zodType.optional();
    }

    shape[key] = zodType;
  }

  return z.object(shape);
}

interface FieldRendererProps {
  name: string;
  prop: JsonSchemaProperty;
  control: UseFormReturn<FieldValues>['control'];
  register: UseFormReturn<FieldValues>['register'];
  errors: FieldErrors<Record<string, unknown>>;
  layout?: 'vertical' | 'horizontal' | 'grid';
  schema: JsonSchema;
}

function FieldRenderer({ name, prop, control, register, errors, layout = 'vertical', schema }: FieldRendererProps) {
  const error = errors[name] as FieldError | undefined;
  const isSecret = prop.format === 'password' || prop.format === 'secret';
  const [showSecret, setShowSecret] = React.useState(false);
  // Boolean mặc định vẽ thành công tắc, không phải ô text — một khoá
  // "true/false" mà để trong ô text thì không ai bật/tắt được.
  const widget =
    prop.ui?.widget ??
    (prop.type === 'boolean' ? 'switch' : 'text');

  const renderInput = () => {
    const baseProps = {
      ...register(name),
      placeholder: prop.ui?.placeholder,
      disabled: prop.ui?.widget === 'hidden',
    };

    switch (widget) {
      case 'textarea':
        return (
          <Textarea
            {...baseProps}
            error={error?.message}
            label={prop.title}
            hint={prop.description}
            rows={4}
          />
        );
      case 'select':
        return (
          <div className="w-full">
            <Label htmlFor={name}>{prop.title}</Label>
            <Controller
              name={name}
              control={control}
              rules={{ required: !!schema.required?.includes(name) }}
              render={({ field }) => (
                <Select value={field.value ?? ''} onValueChange={field.onChange}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select an option" />
                  </SelectTrigger>
                  <SelectContent>
                    {prop.enum?.map((value) => (
                      <SelectItem key={value} value={value}>
                        {value}
                      </SelectItem>
                    ))}
                    {prop.ui?.options?.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
            {error && <p className="mt-1 text-sm text-vnmate-pink">{error.message}</p>}
            {prop.description && <p id={`${name}-help`} className="mt-1 text-sm text-vnmate-slate-500">{prop.description}</p>}
          </div>
        );
      case 'switch':
        return (
          <div className="flex items-center justify-between">
            <div>
              <Label htmlFor={name} className="cursor-pointer">{prop.title}</Label>
              {prop.description && <p className="text-sm text-vnmate-slate-500">{prop.description}</p>}
            </div>
            <Controller
              name={name}
              control={control}
              render={({ field }) => (
                <Switch
                  id={name}
                  checked={field.value ?? false}
                  onCheckedChange={field.onChange}
                />
              )}
            />
          </div>
        );
      case 'hidden':
        return null;
      default:
        return (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Label htmlFor={name} className="cursor-help">
                  {prop.title}
                  {prop.description && <Info className="ml-1 h-3.5 w-3.5 text-vnmate-slate-500" />}
                </Label>
              </TooltipTrigger>
              <TooltipContent side="right" align="start">
                <p className="text-sm text-vnmate-slate-300">{prop.description}</p>
              </TooltipContent>
            </Tooltip>
            <div className="relative">
              <Input
                {...baseProps}
                type={isSecret && !showSecret ? 'password' : 'text'}
                error={error?.message}
                className={isSecret ? 'pr-10' : ''}
              />
              {isSecret && (
                <button
                  type="button"
                  onClick={() => setShowSecret(!showSecret)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-vnmate-slate-400 hover:text-vnmate-neon"
                  aria-label={showSecret ? 'Hide password' : 'Show password'}
                >
                  {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              )}
            </div>
            {error && <p className="mt-1 text-sm text-vnmate-pink flex items-center gap-1"><AlertCircle className="h-3.5 w-3.5" />{error.message}</p>}
            {prop.description && !error && <p id={`${name}-help`} className="mt-1 text-sm text-vnmate-slate-500">{prop.description}</p>}
          </TooltipProvider>
        );
    }
  };

  const fieldClassName = layout === 'horizontal' ? 'w-full' : 'w-full';

  return (
    <div className={cn(fieldClassName, 'animate-in fade-in slide-in-from-top-2 duration-300')}>
      {renderInput()}
    </div>
  );
}

export function DynamicForm({
  schema,
  onSubmit,
  defaultValues = {},
  submitText = 'Save Configuration',
  cancelText = 'Cancel',
  onCancel,
  loading = false,
  showCancel = true,
  className,
  layout = 'vertical',
  cols = 1,
}: DynamicFormProps) {
  const zodSchema = React.useMemo(() => jsonSchemaToZod(schema), [schema]);

  // Lấy giá trị mặc định từ schema khi caller không truyền vào, để form mở ra
  // đã điền sẵn những gì backend khai báo (vd region của AWS). Nếu không gộp
  // ở đây thì `defaultValues={{}}` của caller sẽ xoá sạch mặc định trong
  // schema và người dùng phải tự nhớ region là gì.
  const initialValues = React.useMemo(() => {
    const fromSchema: Record<string, unknown> = {};
    for (const [key, prop] of Object.entries(schema.properties)) {
      if (prop.default !== undefined) fromSchema[key] = prop.default;
    }
    return { ...fromSchema, ...defaultValues };
  }, [schema, defaultValues]);

  const {
    control,
    handleSubmit,
    register,
    formState: { errors, isSubmitting },
  } = useForm<Record<string, unknown>>({
    resolver: zodResolver(zodSchema),
    defaultValues: initialValues,
    mode: 'onChange',
  });

  const handleFormSubmit = async (data: Record<string, unknown>) => {
    await onSubmit(data);
  };

  const fieldNames = Object.keys(schema.properties);

  const getColClass = () => {
    switch (cols) {
      case 1: return 'grid-cols-1';
      case 2: return 'grid-cols-1 md:grid-cols-2';
      case 3: return 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3';
      default: return 'grid-cols-1';
    }
  };

  return (
    <Card className={cn('overflow-hidden', className)}>
      <CardHeader className="border-b border-vnmate-slate-800">
        <CardTitle className="text-vnmate-cyan flex items-center gap-2">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          Dynamic Configuration Form
        </CardTitle>
        <CardDescription>
          Configure plugin settings using the form below. Fields marked with <span className="text-vnmate-pink">*</span> are required.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(handleFormSubmit)} className="space-y-6">
          {layout === 'grid' ? (
            <div className={cn('grid gap-6', getColClass())}>
              {fieldNames.map((name) => (
                <FieldRenderer
                  key={name}
                  name={name}
                  prop={schema.properties[name]}
                  control={control}
                  register={register}
                  errors={errors}
                  layout={layout}
                  schema={schema}
                />
              ))}
            </div>
          ) : (
            <div className="space-y-6">
              {fieldNames.map((name) => (
                <FieldRenderer
                  key={name}
                  name={name}
                  prop={schema.properties[name]}
                  control={control}
                  register={register}
                  errors={errors}
                  layout={layout}
                  schema={schema}
                />
              ))}
            </div>
          )}

          <div className="flex items-center justify-end gap-4 pt-4 border-t border-vnmate-slate-800">
            {showCancel && onCancel && (
              <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting || loading}>
                {cancelText}
              </Button>
            )}
            <Button type="submit" variant="cyber" loading={isSubmitting || loading}>
              {submitText}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function getColClass(cols: 1 | 2 | 3): string {
  switch (cols) {
    case 1: return 'grid-cols-1';
    case 2: return 'grid-cols-1 md:grid-cols-2';
    case 3: return 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3';
    default: return 'grid-cols-1';
  }
}

export function useDynamicForm(schema: JsonSchema, defaultValues?: Record<string, unknown>) {
  const zodSchema = React.useMemo(() => jsonSchemaToZod(schema), [schema]);

  return useForm<Record<string, unknown>>({
    resolver: zodResolver(zodSchema),
    defaultValues: defaultValues,
    mode: 'onChange',
  });
}